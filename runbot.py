import discord
from discord.ext import commands
from discord import app_commands, Intents, Client, Interaction
import os
from io import BytesIO
from types import SimpleNamespace
from classes import Emote, Channel, UserNotFound, InvalidCharacters
import json
import asyncio
import websockets
from websockets.exceptions import InvalidStatus, ConnectionClosedError, WebSocketException
import time

file = open('config.json')
cfg = json.load(file, object_hook=lambda d: SimpleNamespace(**d))
TOKEN = cfg.TOKEN  #Gets TOKEN from config.json
folder_dir = cfg.output_folder
listenchannel_q = asyncio.Queue()
event = asyncio.Event()
ws = None #Define ws variable used in "listen" so we can query this in other commands
emote_set_table = {} #Define emote set table so we can query which emote set belongs to which user

if not os.path.exists(folder_dir):
    os.makedirs(folder_dir)
    print("Output folder created")

if not os.path.exists("tmp"):
    os.makedirs("tmp")
    print("Temporary folder created")
    
class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=cfg.prefix, intents = intents, case_insensitive=cfg.commands_case_insensitive)

    async def setup_hook(self):
        event.set()
    
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.errors.CommandNotFound):
            print(error)
            raise error
            return  # Ignore command not found errors
        elif isinstance(error, commands.errors.EmojiNotFound):
            msg = "The emote specified was not found. Please check the spelling and capitalization and try again"
            await ctx.reply(msg, mention_author=False)
            print(msg)
        elif isinstance(error,commands.errors.NotOwner):
            app_info = await client.application_info()
            botowner = app_info.owner
            msg = f"You are not allowed to run this command. Only the bot owner <@{botowner.id}> can run this command "
            await ctx.reply(msg, mention_author=False)
            print(msg)
        else:
            await ctx.reply(str(error), mention_author=False)
            print(error)
            raise error

client = Bot()

@client.event
async def on_ready():
    print('We have logged in as {0.user}'.format(client))
    await listenchannel_q.put(client.get_channel(cfg.listen_channel))
    event.set()
  
 
@client.event
async def on_message(message):
    if message.author == client.user:
        return
    if message.content.startswith(r'https://old.7tv.app/emotes/'):
        emoteID = message.content.split("/")[-1]
        e = Emote(emoteID,cfg.showemote_size)
        if hasattr(e.info, 'message'):
            await message.channel.send(e.message)
        else:
            e.download("tmp")
            with open(e.file_path, 'rb') as fp:
                await message.channel.send(file=discord.File(fp))
            os.remove(e.file_path)
    await client.process_commands(message)
 
@client.event
async def on_command_error(ctx, error):
    if isinstance(error,commands.errors.EmojiNotFound):
        msg = "The emote specified was not found. Please check the spelling and capitalisation and try again"
        await ctx.reply(msg,mention_author=False)
        print(msg)
    elif isinstance(error,commands.errors.NotOwner):
        app_info = await client.application_info()
        botowner = app_info.owner
        msg = f"You are not allowed to run this command. Only the bot owner <@{botowner.id}> can run this command "
        await ctx.reply(msg, mention_author=False)
        print(msg)
    else:
        await ctx.reply(error, mention_author=False)
        print(error)
        raise error


@client.hybrid_command(name = "addemote", with_app_command = True, description = "Adds an emote to the server you are in using the provided 7TV Link")
@app_commands.describe(url='URL of the emote you want to add',
    emotename= 'The name you want to newly added emote to be called')
@commands.has_permissions(manage_emojis = True)
async def addemote(ctx, url: str, emotename: str = None):
    success = False
    guild = ctx.guild
    ename = "error"
    error = ""
    if ctx.author.guild_permissions.manage_emojis:
        await ctx.defer(ephemeral = cfg.private_response)
        emoteID = url.split("/")[-1]
        for i in reversed(range(1, 5)):
            print(f"Trying size {i}x...")
            e = Emote(emoteID,i)
            if hasattr(e.info, 'message'):
                await ctx.send(e.message)
            else:
                if emotename is None:
                    ename = e.info.name
                else: ename = emotename
                e.download("tmp")
                with open(e.file_path, 'rb') as fp:
                    try:
                        img_or_gif = BytesIO(fp.read())
                        b_value = img_or_gif.getvalue()
                        emoji = await guild.create_custom_emoji(image=b_value, name=ename)
                        print(f'Successfully added emote: <:{ename}:{emoji.id}>')
                        await ctx.send(f'Successfully added emote: <:{ename}:{emoji.id}>')
                        success = True

                    except Exception as err:
                        error = err
                        print(err)

                if os.path.exists(e.file_path):
                    os.remove(e.file_path)

                if success: break
        if not success:
            await ctx.send(f'Unable to add emote {ename}: {error}')


@client.hybrid_command(name = "removeemote", with_app_command = True, description = "Remove a discord emote from the server by specifying the name/emote")
@app_commands.describe(emote='Name/Emote you want to delete')
@commands.has_permissions(manage_emojis = True)
async def removeemote(ctx, emote: discord.Emoji):
    if ctx.author.guild_permissions.manage_emojis:
        await ctx.defer(ephemeral = cfg.private_response)
        await ctx.guild.delete_emoji(emote)
        msg = (f'Successfully deleted: {emote}')
        print(msg)
        await ctx.send(msg)

@client.hybrid_command(name= "servers", with_app_command = True, description = "Lists the servers that the bot is in")
@commands.is_owner()
async def servers(ctx):
    servers = list(client.guilds)
    await ctx.send(f"Number of servers I am in: {str(len(servers))}\n" + "\n".join([server.name for server in servers]))


@client.hybrid_command(name = "findemoteinchannel", with_app_command = True, description = "Find emotes in a specific Twitch channel's 7TV emotes")#
@app_commands.describe(channel='Channel you want to search emotes in',
    emote= 'Text you want to search for',
    exact='Do you want the text to match exactly?')
@commands.has_permissions(manage_emojis = True)
async def findemoteinchannel(ctx, channel: str, emote: str, exact= False):
    print(f"FindEmoteQuery = Channel:{channel} Emote:{emote}")
    await ctx.defer(ephemeral = cfg.private_response)
    try:
        c = Channel(channel)
    except UserNotFound:
        await ctx.send("User not found. Please check the username and try again")
    else:
        elist = c.findEmotes(emote, exact)
        message = f'{channel} has {len(elist)} {emote} emote(s):'
        for i in elist:
            print(f"Found {i.name}")
            message += f"\n[{i.name}](https://7tv.app/emotes/{i.id})"

        embed = discord.Embed(
            title= "Search completed!",
            description= message,
            colour= discord.Colour.from_rgb(40,177,166)
    )

        embed.set_thumbnail(url=ctx.me.display_avatar.url)
        embed.set_footer(text="You can also add the emotes to your server as emoji by: !addemote <emote url>")
        await ctx.send(embed= embed)


@client.hybrid_command(name = "searchemotes", with_app_command = True, description = "Search for emotes using the name")
@app_commands.describe(emote='Name/Emote you want to search for')
@commands.has_permissions(manage_emojis = True)
async def searchemotes(ctx, emote: str):
    message = ""
    if ctx.author.guild_permissions.manage_emojis:
        await ctx.defer(ephemeral = cfg.private_response)
        elist = Emote.searchemotes(emote)
        message += f'Found {len(elist)} emote(s) that contain "{emote}":'
        for i in elist:
            print(f"Found {i.name}")
            message += f"\n[{i.name}](https://7tv.app/emotes/{i.id})"

        embed = discord.Embed(
            title= "Search completed!",
            description= message,
            colour= discord.Colour.from_rgb(40,177,166)
        )

        embed.set_thumbnail(url=ctx.me.display_avatar.url)
        embed.set_footer(text="You can also add the emotes to your server as emoji by: !addemote <emote url>")
        await ctx.send(embed= embed)


@client.hybrid_command(name = "addlistenchannel", with_app_command = True, description = "Add a Twitch channel to listen for 7TV emote updates")
@app_commands.describe(channel='Name of the channel you want to add to the listening channels')
@commands.has_permissions(manage_emojis = True)
async def addlistenchannel(ctx, channel: str):
    if ctx.author.guild_permissions.manage_emojis:
        await ctx.defer(ephemeral = cfg.private_response)
        await event.wait()
        await asyncio.sleep(1)
        try:
            c = Channel(channel)
        except UserNotFound:
            msg = f"The channel {channel} is not found. Please check the spelling and try again."
            print(msg)
            await ctx.send(msg)
            event.set()
            return
        except InvalidCharacters:
            msg = f"The query {channel} contains invalid characters. Please only use A-Z and numbers."
            print(msg)
            await ctx.send(msg)
            event.set()
            return
        if c.id in cfg.listeningUsers:
            msg = f"The channel {channel} already exists in this list!"
            print(msg)
            await ctx.send(msg)
        else:
            try:
                cfg.listeningUsers.append(c.id) #Add the 7TV ID to the "Config.json"
                with open('config.json', 'w', encoding='utf-8') as f:
                    json.dump(cfg.__dict__, f, ensure_ascii=False, indent=4)
                try:
                    if ws != None: #If ws is not "None" (has been called by "listen") then send op code 35 and listen to the new channel
                        # Decide which emote sets to subscribe
                        if cfg.subscribe_all_emote_sets:
                            emote_sets_to_subscribe = c.parsed.user.emote_sets
                        else:
                            emote_sets_to_subscribe = [next(
                                set for set in c.parsed.user.emote_sets if set.id == c.active_set_id
                            )]

                        for emote_set in emote_sets_to_subscribe:
                            emote_set.name = "Default" if emote_set.id == c.id else emote_set.name
                            emote_set_table[emote_set.id] = (c.name, emote_set.name)
                            await ws.send(json.dumps({
                                "op": 35,
                                "d": {
                                    "type": "emote_set.update",
                                    "condition": {"object_id": emote_set.id}
                                }
                            }))
                            await asyncio.sleep(0.25)
                except Exception as err:
                    print(err)
                    await ctx.send(err)
                    event.set()
                print((f'Added {c.name} to listening channels'))
                await ctx.send(f'Added {c.name} to listening channels')
            except Exception as err:
                print(err)
                await ctx.send(err)
                event.set()
        event.set()


@client.hybrid_command(name = "removelistenchannel", with_app_command = True, description = "Remove a Twitch channel to listen for 7TV emote updates")
@app_commands.describe(channel='Name of the channel you want to remove from the listening channels')
@commands.has_permissions(manage_emojis = True)
async def removelistenchannel(ctx, channel: str):
    if ctx.author.guild_permissions.manage_emojis:
        await ctx.defer(ephemeral = cfg.private_response)
        await event.wait()
        await asyncio.sleep(1)
        try:
            c = Channel(channel)
        except UserNotFound:
            msg = f"The channel {channel} is not found. Please check the spelling and try again."
            print(msg)
            await ctx.send(msg)
            event.set()
            return
        except InvalidCharacters:
            msg = f"The query {channel} contains invalid characters. Please only use A-Z and numbers."
            print(msg)
            await ctx.send(msg)
            event.set()
            return
        if c.id in cfg.listeningUsers:
            try:
                cfg.listeningUsers.remove(c.id) #Remove the 7TV ID to the "Config.json"
                with open('config.json', 'w', encoding='utf-8') as f:
                    json.dump(cfg.__dict__, f, ensure_ascii=False, indent=4)
                try:
                    if ws != None: #If ws is not "None" (has been called by "listen") then send op code 36 and stop listening to the new channel
                        if cfg.subscribe_all_emote_sets:
                            emote_sets_to_unsubscribe = c.parsed.user.emote_sets
                        else:
                            emote_sets_to_unsubscribe = [next(
                                set for set in c.parsed.user.emote_sets if set.id == c.active_set_id
                            )]

                        for emote_set in emote_sets_to_unsubscribe:
                            emote_set.name = "Default" if emote_set.id == c.id else emote_set.name
                            emote_set_table.pop(emote_set.id, None)
                            await ws.send(json.dumps({
                            "op": 36,
                            "d": {
                                "type": "emote_set.update",
                                "condition": {
                                    "object_id": c.id
                                }
                            }
                            }))
                            await asyncio.sleep(0.25) 
                except Exception as err:
                    print(err)
                    await ctx.send(err)
                    event.set()
                msg = f'Removed {c.name} from listening channels'
                print(msg)
                await ctx.send(msg)
            except Exception as err:
                print(err)
                await ctx.send(err)
                event.set()
        else:
            msg = f"The channel {channel} does not exist in this list!"
            print(msg)
            await ctx.send(msg)
        event.set()

@client.hybrid_command(name = "query7tvchannel", with_app_command = True, description = "Looks up a 7TV Channel based on the username provided.")
@app_commands.describe(channel='Name of the channel you want to query')
@commands.has_permissions(manage_emojis=True)
async def query7tvchannel(ctx, channel: str):
    await ctx.defer(ephemeral=cfg.private_response)

    try:
        c = Channel(channel)
    except UserNotFound:
        msg = f"The channel {channel} is not found. Please check the spelling and try again."
    except InvalidCharacters:
        msg = f"The query {channel} contains invalid characters. Please only use A-Z and numbers."
    else:
        if hasattr(c, 'id'):
            msg = f"Found channel {c.name}: https://7tv.app/users/{c.id}"
        else:
            msg = f"Unhandled error for {channel}"

    print(msg)
    await ctx.send(msg)  

@client.hybrid_command(name = "listeningchannels", with_app_command = True, description = "Show Twitch channels that the bot is listening to for 7TV emote updates.")
@commands.has_permissions(manage_emojis = True)
async def listeningchannels(ctx):
    msg = "Currently listening: "
    if ctx.author.guild_permissions.manage_emojis:
        await ctx.defer(ephemeral = cfg.private_response)
        if cfg.listeningUsers:
            msg = "Currently listening: "
            lc = [Channel.lookup7TVUser(i) for i in cfg.listeningUsers]
            for i in lc[:-1]:
                msg += f'{i}, '
            msg += f'{lc[-1]}. '
    await ctx.send(msg,ephemeral=False)


@client.command()
@commands.guild_only()
@commands.is_owner()
async def sync(ctx):
    """Syncs the Slash commands to Discord globally. Be careful not to spam this as this is rate limited and slow to propagate changes"""
    print(f"User {ctx.message.author} ID:{ctx.message.author.id} ran command. Asking for confirmation")
    def check(msg): # checking if it's the same user and channel
        return msg.author == ctx.author and msg.channel == ctx.channel
    try:
        msg = ("Are you sure you want to run a global sync?")
        print(msg)
        await ctx.send(msg)        
        response = await client.wait_for('message', check=check, timeout=30.0)
    except asyncio.TimeoutError: # returning after timeout
        msg = (f"No response from {ctx.message.author}. Cancelling sync.")
        print(msg)
        await ctx.send(msg)        
        return
    if response.content.lower() not in ("yes", "y"): # lower() makes everything lowercase to also catch: YeS, YES etc.
        msg = (f"User {ctx.message.author} did not enter 'Yes' or 'Y'. Cancelling sync.")
        print(msg)
        await ctx.send(msg)        
        return
    print(f"User {ctx.message.author} ran 'sync' (Global) " )
    await ctx.defer(ephemeral = cfg.private_response)
    await client.tree.sync()
    msg = (f"Synced slash commands globally for {client.user}.")
    print(msg)
    await ctx.send(msg)        

async def listen():
    max_backoff_time = 120  # Maximum backoff time in seconds
    exception_max_backoff_time = 600  # Maximum backoff time for exceptions in seconds
    backoff_factor = 2  # Backoff factor for exceptions
    exception_consecutive_503 = 0  # Counter for consecutive 503 exceptions
    exception_consecutive_exceptions = 0  # Counter for consecutive exceptions
    global ws
    accessPt = "wss://events.7tv.io/v3"
    listenchannel = await listenchannel_q.get()
    title = ""
    message = ""
    color = discord.Colour.from_rgb(40, 177, 166)
    e = None
    eurl = ""
    backoff_time = 2  # Initial backoff time in seconds
    is_connected = True
    
    while True:
        if listenchannel:
            try:
                async with websockets.connect(accessPt) as ws:
                    for user_id in cfg.listeningUsers:
                        channelUsername = Channel.lookup7TVUser(user_id)
                        channel = Channel(channelUsername)
                        # Loop through emote sets if cfg.subscribe_all_emote_sets eq true 
                        if cfg.subscribe_all_emote_sets:
                            # Grab all the current emote sets
                            emote_sets_to_subscribe = channel.parsed.user.emote_sets
                        else:
                            # Grab just the active emote set instead
                            active_emote_set = next(
                                (set for set in channel.parsed.user.emote_sets if set.id == channel.active_set_id),
                                None
                            )
                            emote_sets_to_subscribe = [active_emote_set] if active_emote_set else []
                        # Read the parsed data and extract relevant emote sets. Store using emote_set_id as the primary key and the data as "username","emote set name". 
                        for emote_set in emote_sets_to_subscribe:
                            emote_set.name = "Default" if emote_set.id == user_id else emote_set.name
                            print(f"Subscribed to {channelUsername} - {emote_set.name}")
                            emote_set_table[emote_set.id] = (channel.name, emote_set.name)
                            # Send WS subscribe command for each emote set in the list
                            await ws.send(json.dumps({
                                "op": 35,
                                "d": {
                                    "type": "emote_set.update",
                                    "condition": {
                                        "object_id": emote_set.id
                                    }
                                }
                            }))
                            await asyncio.sleep(0.25) 
                    if not is_connected:
                        app_info = await client.application_info()
                        if app_info.owner:
                            owner = app_info.owner
                            await owner.send(f"The service is back up! Had {exception_consecutive_503} 503's before reconnection. Last backoff time was {backoff_time}")  # Send message when connection is reestablished
                        is_connected = True
                    backoff_time = 2  # Reset backoff time after successful connection
                    exception_consecutive_503 = 0  # Counter for consecutive 503 exceptions
                    exception_consecutive_exceptions = 0  # Reset consecutive exception counter
                    while event.is_set():
                        msg = await ws.recv()
                        parsed = json.loads(msg)
                        parsed = parsed['d']
                        if "body" in parsed:
                            username, emote_set_name = emote_set_table.get(parsed['body']['id'], ("Unknown Username", "Unknown Emote Set"))
                            title = f"{username} - {emote_set_name}"
                            if "pushed" in parsed['body']:
                                e = Emote(parsed['body']['pushed'][0]['value']['id'], 3)
                                message = f"The user {parsed['body']['actor']['username'].lower().capitalize()} added emote to \"{emote_set_name}\"\n{parsed['body']['pushed'][0]['value']['name']}:\nhttps://7tv.app/emotes/{e.id}"
                                color = discord.Colour.from_rgb(40, 177, 166)
                                embedmessage = f"/addemote url:https://7tv.app/emotes/{e.id} emotename:{parsed['body']['pushed'][0]['value']['name']}"

                            elif "pulled" in parsed['body']:
                                e = Emote(parsed['body']['pulled'][0]['old_value']['id'], 3)
                                message = f"The user {parsed['body']['actor']['username'].lower().capitalize()} removed emote from \"{emote_set_name}\"\n{parsed['body']['pulled'][0]['old_value']['name']}:\nhttps://7tv.app/emotes/{e.id} "
                                color = discord.Colour.from_rgb(177, 40, 51)
                                embedmessage = f"/removeemote emote::{parsed['body']['pulled'][0]['old_value']['name']}:"
                            # Emote set rename handling
                            elif "updated" in parsed['body']:
                                for item in parsed['body']['updated']:
                                    if item['key'] == "name":
                                        old_name = item['old_value']
                                        new_name = item['value']
                                        emote_set_table[parsed['body']['id']] = (username, new_name)
                                        message = f"Emote set renamed: \"{old_name}\" → \"{new_name}\""
                                        color = discord.Colour.from_rgb(40, 177, 166)
                                        embedmessage = f"Emote set rename by {parsed['body']['actor']['username'].capitalize()}"
                                        break
                            # Catch-all for unknown emote_set.update events
                            else:
                                owner = (await client.application_info()).owner
                                if owner:
                                    try:
                                        # Grab the 4th value in the body dict
                                        body_values = list(parsed['body'].values())
                                        fourth_value = body_values[3] if len(body_values) > 3 else parsed['body']
                                        # Also get the corresponding key
                                        body_keys = list(parsed['body'].keys())
                                        fourth_key = body_keys[3] if len(body_keys) > 3 else "unknown"
                                    except IndexError:
                                        fourth_value = parsed['body']
                                        fourth_key = "unknown"

                                    await owner.send(
                                        f"Unhandled emote_set.update event for emote set {parsed['body'].get('id','Unknown')}\nKey: {fourth_key}\n```{fourth_value}```"
                                    )
                                continue  # Silently ignore for bot
                             # Generate emote URL but only if applicable
                            eurl = f"https://cdn.7tv.app/emote/{e.id}/3x.gif" if isinstance(e, Emote) and e.isAnimated else \
                                   f"https://cdn.7tv.app/emote/{e.id}/3x.png" if isinstance(e, Emote) else None
                            embed = discord.Embed(
                                title=title,
                                description=message,
                                colour=color
                            )

                            if eurl:
                                embed.set_thumbnail(url=eurl)
                            embed.set_footer(text=embedmessage)
                            await listenchannel.send(embed=embed)

                    while not event.is_set():
                        await asyncio.sleep(5)
            except (InvalidStatus, ConnectionClosedError) as e:
                if isinstance(e, InvalidStatus) and e.status_code in {503, 403, 521}:
                    if is_connected:
                        app_info = await client.application_info()
                        if app_info.owner:
                            owner = app_info.owner
                            await owner.send(f" e.status_code The service is currently unavailable. ")  # Send message when connection is down
                        is_connected = False
                    print(f" e.status_code Service Unavailable. Reconnecting in {backoff_time} seconds...")
                    await asyncio.sleep(backoff_time)
                    backoff_time *= backoff_factor  # Exponential backoff
                    backoff_time = min(backoff_time, max_backoff_time)  # Limit backoff time to max_backoff_time
                    exception_consecutive_exceptions = 0  # Reset consecutive exception counter
                    exception_consecutive_503 += 1
                elif isinstance(e, ConnectionClosedError):
                    if e.code == 4012:
                        await asyncio.sleep(2)  # Add a delay before reconnecting
                        print(f"Received 4012 (Reboot) cloudflare status code. Reconnecting in 2 seconds...")
                        continue
                    elif e.code == 1001:
                        print(f"Received 1001 (Reboot) status code. Reconnecting in 2 seconds...")
                        await asyncio.sleep(2)  # Add a delay before reconnecting
                        app_info = await client.application_info()
                        if app_info.owner:
                            owner = app_info.owner
                            await owner.send(f"Received 1001 and handled cleanly")
                        continue
                    else:
                        print(f"Received unexpected close code {e.code}. Reconnecting in 2 seconds...")
                        await asyncio.sleep(2)  # Add a delay before reconnecting
                        continue
                else:
                    print(f"An error occurred. Backoff time is {backoff_time} seconds. Error Details: {str(e)}")
                    print(f"WebSocket connection closed. Uncaught error {str(e)}. Reconnecting in 60 seconds...")
                    exception_consecutive_exceptions += 1  # Increment consecutive exception counter
                    if exception_consecutive_exceptions >= 5:  # Change the value to the number of consecutive exceptions before increasing the backoff time
                        backoff_time *= backoff_factor  # Exponential backoff
                        backoff_time = min(backoff_time, exception_max_backoff_time)  # Limit backoff time to exception_max_backoff_time
                        app_info = await client.application_info()
                        if app_info.owner:
                            owner = app_info.owner
                            await owner.send(f"Unhandled WebSocket exception occurred. Backoff time is {backoff_time} seconds.\nError Details:\n```{str(e)}.```")
                    await asyncio.sleep(backoff_time)
            except WebSocketException as e:
                print(f"WebSocket exception occurred: {str(e)}")
                exception_consecutive_exceptions += 1  # Increment consecutive exception counter
                if hasattr(e, 'code') and e.code == 1001:
                    # Handle code 1001 gracefully with a short reconnect delay
                    print("Received 1001 (going away) status code. Reconnecting in 10 seconds...")
                    await asyncio.sleep(10)
                    continue  # Attempt to reconnect without increasing backoff time 
                if exception_consecutive_exceptions >= 1:  # Change the value to the number of consecutive exceptions before increasing the backoff time
                    backoff_time *= backoff_factor  # Exponential backoff
                    exception_consecutive_exceptions = 0  # Reset consecutive exception counter
                    backoff_time = min(backoff_time, exception_max_backoff_time)  # Limit backoff time to exception_max_backoff_time
                    app_info = await client.application_info()
                    if app_info.owner:
                        owner = app_info.owner
                        await owner.send(f"Unhandled WebSocket exception occurred:\n```{str(e)}\nCode: {str(e.code)}\nDict: {e.__dict__}```")
            except Exception as e:
                print(f"An error occurred. Backoff time is {backoff_time} seconds. Error Details: {str(e)}")
                exception_consecutive_exceptions += 1  # Increment consecutive exception counter
                if exception_consecutive_exceptions >= 5:  # Change the value to the number of consecutive exceptions before increasing the backoff time
                    backoff_time *= backoff_factor  # Exponential backoff
                    backoff_time = min(backoff_time, exception_max_backoff_time)  # Limit backoff time to exception_max_backoff_time
                    app_info = await client.application_info()
                    if app_info.owner:
                        owner = app_info.owner
                        await owner.send(f"Unhandled exception occurred. Backoff time is {backoff_time} seconds.\nError Details:\n```{str(e)}.```")
                await asyncio.sleep(backoff_time)


loop = asyncio.new_event_loop()


async def run_bot():
    try:
        await client.start(TOKEN)
    except Exception:
        await client.close()


def run_in_thread():
    fut = asyncio.run_coroutine_threadsafe(listen(), loop)
    fut.result()  # wait for the result


async def main():
    await asyncio.gather(
        run_bot(),
        asyncio.to_thread(run_in_thread)
    )


loop.run_until_complete(main())
