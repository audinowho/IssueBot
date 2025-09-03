import os
import discord
import traceback
import json
import git
import sys
import IssueUtils


# Housekeeping for login information
TOKEN_FILE_PATH = 'token.txt'
CONFIG_FILE_PATH = 'config.json'
PRIVATE_KEY_FILE_PATH = 'private-key.pem'

scdir = os.path.dirname(os.path.abspath(__file__))

# The Discord client.
intent = discord.Intents.default()
intent.message_content = True
client = discord.Client(intents=intent)

class BotServer:

    def __init__(self, main_dict=None):
        self.issue = 0
        self.chat = 0
        self.after_post = 0
        self.threads = []
        self.prefix = ""

        if main_dict is None:
            return

        for key in main_dict:
            self.__dict__[key] = main_dict[key]

    def getDict(self):
        return self.__dict__

class BotConfig:

    def __init__(self, main_dict=None):
        self.root = 0
        self.error_ch = 0
        self.update_ch = 0
        self.update_msg = 0
        self.repo_owner = ""
        self.repo_name = ""
        self.app_id = ""
        self.install_id = ""
        self.servers = {}

        if main_dict is None:
            return

        for key in main_dict:
            self.__dict__[key] = main_dict[key]

        sub_dict = {}
        for key in self.servers:
            sub_dict[key] = BotServer(self.servers[key])
        self.servers = sub_dict

    def getDict(self):
        node_dict = { }
        for k in self.__dict__:
            node_dict[k] = self.__dict__[k]
        sub_dict = { }
        for sub_idx in self.servers:
            sub_dict[sub_idx] = self.servers[sub_idx].getDict()
        node_dict["servers"] = sub_dict
        return node_dict

class IssueBot:
    """
    A class for handling issues
    """
    def __init__(self, in_path, client):
        # init data
        self.path = in_path
        self.need_restart = False
        with open(os.path.join(self.path, CONFIG_FILE_PATH)) as f:
            self.config = BotConfig(json.load(f))
        self.private_key = open(PRIVATE_KEY_FILE_PATH, 'r').read()


        self.client = client

        print("Info Initiated")

    def saveConfig(self):
        with open(os.path.join(self.path, CONFIG_FILE_PATH), 'w', encoding='utf-8') as txt:
            config = self.config.getDict()
            json.dump(config, txt, indent=2)

    async def updateBot(self, msg):
        resp_ch = self.getChatChannel(msg.guild.id)
        resp = await resp_ch.send("Pulling from repo...")
        # update self
        bot_repo = git.Repo(scdir)
        origin = bot_repo.remotes.origin
        origin.pull()
        await resp.edit(content="Update complete! Bot will restart.")
        self.need_restart = True
        self.config.update_ch = resp_ch.id
        self.config.update_msg = resp.id
        self.saveConfig()
        await self.client.logout()

    async def checkRestarted(self):
        if self.config.update_ch != 0 and self.config.update_msg != 0:
            msg = await self.client.get_channel(self.config.update_ch).fetch_message(self.config.update_msg)
            await msg.edit(content="Bot updated and restarted.")
            self.config.update_ch = 0
            self.config.update_msg = 0
            self.saveConfig()

    async def sendError(self, trace):
        print(trace)
        to_send = await self.client.fetch_user(self.config.root)
        if self.config.error_ch != 0:
            to_send = self.client.get_channel(self.config.error_ch)

        await to_send.send("```" + trace[:1950] + "```")

    def getChatChannel(self, guild_id):
        chat_id = self.config.servers[str(guild_id)].chat
        return self.client.get_channel(chat_id)


    async def isAuthorized(self, user, guild):

        if user.id == self.client.user.id:
            return False
        if user.id == self.config.root:
            return True

        return False

    async def respondInvalid(self, issue_reporter, msg):
        if msg.author.id == issue_reporter:
            await msg.add_reaction('\U0000274C')
        else:
            remove_users = []
            for reaction in msg.reactions:
                async for user in reaction.users():
                    if user.id == issue_reporter:
                        remove_users.append((reaction, user))

            for reaction, user in remove_users:
                await reaction.remove(user)

    async def pushIssue(self, msg, labels):
        args = msg.content.split(' ')
        title = " ".join(args[1:])
        issue_msg = await self.client.get_channel(msg.reference.channel_id).fetch_message(msg.reference.message_id)
        await msg.delete()
        # push issue to git
        header = IssueUtils.get_access_token_header(self.private_key, self.config.app_id, self.config.install_id)
        body = "Discord: {0}#{1} {2}".format(issue_msg.author.name, issue_msg.author.discriminator,
                                              issue_msg.author.mention)
        body += "\n\n"
        body += issue_msg.content
        for attachment in issue_msg.attachments:
            body += "\n![image]({0})".format(attachment.url)

        url = IssueUtils.create_issue(header, self.config.repo_owner, self.config.repo_name, title, body, labels)
        # react with a star... and a reply?
        await issue_msg.add_reaction('\U000021A9')


    async def linkEarliestUnresolved(self, msg):

        server = self.config.servers[str(msg.guild.id)]
        ch_id = server.issue
        earliest_unresolved = server.after_post
        channel = self.client.get_channel(ch_id)
        prevMsg = None
        while True:
            count = 0
            async for message in channel.history(limit=100, before=prevMsg):
                count += 1
                prevMsg = message
                if message.id < server.after_post:
                    count = 0
                    break
                try:
                    needs_attention = await self.checkNeedsAttention(message)
                    if needs_attention:
                        earliest_unresolved = message.id
                except Exception as e:
                    await self.sendError(traceback.format_exc())
            if count == 0:
                break

        await msg.channel.send(msg.author.mention + "Earliest unresolved issue: https://discord.com/channels/{0}/{1}/{2}".format(msg.guild.id, ch_id, earliest_unresolved))

        server.after_post = earliest_unresolved
        self.saveConfig()


    async def checkNeedsAttention(self, msg):
        # check for messages in #bug-reports
        if msg.author.bot:
            return False

        if msg.type == discord.MessageType.thread_created:
            return False

        reacted = False
        for reaction in msg.reactions:
            async for user in reaction.users():
                if user.id == self.config.root:
                    reacted = True
                elif user.id == self.client.user.id:
                    reacted = True
                #else:
                #    remove_users.append((reaction, user))

        return not reacted


    async def beginIssue(self, msg):
        # create the thread
        thread = await msg.create_thread(name=msg.content.split('\n')[0][:50])
        # Click :leftwards_arrow_with_hook: to undo the last answer.
        return_txt = "Thread created.  The bot will ask some questions.  Answering them will expedite the process."
        survey_msg = await thread.send(return_txt)
        return_txt = msg.author.mention + "\n1. Is this a :beetle: Bug, :bulb: Feature Request, or :abc: Text Mistake?"
        survey_msg = await thread.send(return_txt)
        await survey_msg.add_reaction('\U0001FAB2')
        await survey_msg.add_reaction('\U0001F4A1')
        await survey_msg.add_reaction('\U0001F524')

        server = self.config.servers[str(msg.guild.id)]
        server.threads.append(thread.id)
        self.saveConfig()

    async def getCurrentStep(self, thread):
        server = self.config.servers[str(thread.guild.id)]

        if thread.id in server.threads:
            # get the latest message written by the bot
            async for message in thread.history(limit=None):
                if message.author.id == self.client.user.id:
                    message_lines = message.content.split('\n')
                    mention = message.mentions[0].id
                    prefix = message_lines[1].split('.')[0]
                    return mention, prefix, message

        return None, None, None


    async def moveToNextStep(self, issue_reporter, prefix, msg):
        thread = msg.channel
        completed = False

        if prefix == "1":
            if await self.chose_emoji(issue_reporter, msg, '\U0001FAB2'):
                return_txt = "<@!{0}>".format(issue_reporter) + "\n2. Please attach the log file for this error.  Logs are found in the `LOG/` folder.  Attach the `.txt` file with the date that matches when you encountered the bug.\nIf this bug occurred outside of the game (such as with the updater), click :x:"
                survey_msg = await thread.send(return_txt)
                # await survey_msg.add_reaction('\U000021A9')
                await survey_msg.add_reaction('\U0000274C')
            elif await self.chose_emoji(issue_reporter, msg, '\U0001F4A1') or await self.chose_emoji(issue_reporter, msg, '\U0001F524'):
                completed = True
            else:
                await self.respondInvalid(issue_reporter, msg)
        elif prefix == "2":
            if self.has_attachment(issue_reporter, msg, '.txt'):
                return_txt = "<@!{0}>".format(issue_reporter) + "\n3. Was this bug was encountered in a dungeon adventure?"
                survey_msg = await thread.send(return_txt)
                # await survey_msg.add_reaction('\U000021A9')
                await survey_msg.add_reaction('\U00002705')
                await survey_msg.add_reaction('\U0000274C')
            elif await self.chose_emoji(issue_reporter, msg, '\U0000274C'):
                completed = True
            else:
                await self.respondInvalid(issue_reporter, msg)
        elif prefix == "3":
            if await self.chose_emoji(issue_reporter, msg, '\U00002705'):
                return_txt = "<@!{0}>".format(issue_reporter) + "\n3a. Did you :checkered_flag: finish that adventure, or are you still :flag_white: in the middle of it?"
                survey_msg = await thread.send(return_txt)
                await survey_msg.add_reaction('\U0001F3C1')
                await survey_msg.add_reaction('\U0000FE0F')
            elif await self.chose_emoji(issue_reporter, msg, '\U0000274C'):
                return_txt = "<@!{0}>".format(issue_reporter) + "\n4. Was this bug encountered while :video_game: Playing or :pencil: Editing the game?"
                survey_msg = await thread.send(return_txt)
                # await survey_msg.add_reaction('\U000021A9')
                await survey_msg.add_reaction('\U0001F3AE')
                await survey_msg.add_reaction('\U0001F4DD')
            else:
                await self.respondInvalid(issue_reporter, msg)
        elif prefix == "3a":
            if await self.chose_emoji(issue_reporter, msg, '\U0001F3C1'):
                return_txt = "<@!{0}>".format(issue_reporter) + "\n3b. Please attach a replay (`.rsrec`) of the adventure.\nCheck replays ingame at the Title Menu under Records, and find the files themselves in the `REPLAY/` folder.\nMake sure the error shows up in the replay."
                survey_msg = await thread.send(return_txt)
            elif await self.chose_emoji(issue_reporter, msg, '\U0000FE0F'):
                return_txt = "<@!{0}>".format(issue_reporter) + "\n3c. Please attach your quicksave file (`QUICKSAVE.rsqs`).  You can find it in the `SAVE/` folder."
                survey_msg = await thread.send(return_txt)
            else:
                await self.respondInvalid(issue_reporter, msg)
        elif prefix == "3b":
            if self.has_attachment(issue_reporter, msg, '.rsrec'):
                completed = True
            else:
                await self.respondInvalid(issue_reporter, msg)
        elif prefix == "3c":
            if self.has_attachment(issue_reporter, msg, '.rsqs'):
                completed = True
            else:
                await self.respondInvalid(issue_reporter, msg)
        elif prefix == "4":
            if await self.chose_emoji(issue_reporter, msg, '\U0001F4DD'):
                return_txt = "<@!{0}>".format(issue_reporter) + "\n4a. Starting from when you open the game, can you list the exact steps to reproduce this issue?  :x: if this was already mentioned."
                survey_msg = await thread.send(return_txt)
                # await survey_msg.add_reaction('\U000021A9')
                await survey_msg.add_reaction('\U0000274C')
            elif await self.chose_emoji(issue_reporter, msg, '\U0001F3AE'):
                return_txt = "<@!{0}>".format(issue_reporter) + "\n5. Please attach your save file.  You can find it in the `SAVE/` folder named `SAVE.rssv`"
                survey_msg = await thread.send(return_txt)
                # await survey_msg.add_reaction('\U000021A9')
            else:
                await self.respondInvalid(issue_reporter, msg)
        elif prefix == "4a":
            if self.sent_any_text(issue_reporter, msg):
                completed = True
            elif await self.chose_emoji(issue_reporter, msg, '\U0000274C'):
                completed = True
            else:
                await self.respondInvalid(issue_reporter, msg)
        elif prefix == "5":
            if self.has_attachment(issue_reporter, msg, '.rssv'):
                return_txt = "<@!{0}>".format(issue_reporter) + "\n5a. Starting from when you load your save file, can you list the exact steps to reproduce this issue?  :x: if this was already mentioned."
                survey_msg = await thread.send(return_txt)
                # await survey_msg.add_reaction('\U000021A9')
                await survey_msg.add_reaction('\U0000274C')
            else:
                await self.respondInvalid(issue_reporter, msg)
        elif prefix == "5a":
            if self.sent_any_text(issue_reporter, msg):
                completed = True
            elif await self.chose_emoji(issue_reporter, msg, '\U0000274C'):
                completed = True
            else:
                await self.respondInvalid(issue_reporter, msg)

        if completed:
            return_txt = "Questionaire complete! You can continue to post information from here on if you have updates."
            survey_msg = await thread.send(return_txt)
            # await survey_msg.add_reaction('\U000021A9')
            server = self.config.servers[str(msg.guild.id)]
            server.threads.remove(thread.id)
            self.saveConfig()

    async def chose_emoji(self, issue_reporter, msg, emoji):

        if msg.author.id != self.client.user.id:
            return False

        # go through users of the specified emoji
        for reaction in msg.reactions:
            if reaction.emoji == emoji:
                async for user in reaction.users():
                    # if the issue reporter's response is found, return true
                    if user.id == issue_reporter:
                        return True

        return False

    def has_attachment(self, issue_reporter, msg, extension):

        if msg.author.id != issue_reporter:
            return False

        if len(msg.attachments) == 0:
            return False

        file_name = msg.attachments[0].filename

        # check the attachment.  if is has the extension, return true
        _, ext = os.path.splitext(file_name)
        if ext == extension:
            return ext

        return True

    def sent_any_text(self, issue_reporter, msg):

        if msg.author.id != issue_reporter:
            return False

        if msg.content == "":
            return False

        return True

    async def initServer(self, msg, args):

        if len(args) != 3:
            await msg.channel.send(msg.author.mention + " Args not equal to 3!")
            return

        if len(msg.channel_mentions) != 2:
            await msg.channel.send(msg.author.mention + " Bad channel args!")
            return

        prefix = args[0]
        issue_ch = msg.channel_mentions[0]
        bot_ch = msg.channel_mentions[1]

        init_guild = msg.guild

        info_perms = issue_ch.permissions_for(init_guild.me)
        bot_perms = bot_ch.permissions_for(init_guild.me)

        if not info_perms.send_messages or not info_perms.read_messages:
            await msg.channel.send(msg.author.mention + " Bad channel perms for info!")
            return

        if not bot_perms.send_messages or not bot_perms.read_messages:
            await msg.channel.send(msg.author.mention + " Bad channel perms for chat!")
            return

        new_server = BotServer()
        new_server.prefix = prefix
        new_server.issue = issue_ch.id
        new_server.chat = bot_ch.id
        self.config.servers[str(init_guild.id)] = new_server

        self.saveConfig()
        await msg.channel.send(msg.author.mention + " Initialized bot to this server!")

    async def help(self, msg, args):
        prefix = self.config.servers[str(msg.guild.id)].prefix
        if len(args) == 0:
            return_msg = "**Commands**\n" \
                  f"`{prefix}help` - Help\n"
        else:
            base_arg = args[0]
            if base_arg == "help":
                return_msg = "**Command Help**\n"
            else:
                return_msg = "Unknown Command."
        await msg.channel.send(msg.author.mention + " {0}".format(return_msg))


    async def staffhelp(self, msg, args):
        prefix = self.config.servers[str(msg.guild.id)].prefix
        if len(args) == 0:
            return_msg = "**Approver Commands**\n" \
                  f"`{prefix}help` - Help\n"
        else:
            base_arg = args[0]
            if base_arg == "help":
                return_msg = "**Command Help**\n"
            else:
                return_msg = "Unknown Command."
        await msg.channel.send(msg.author.mention + " {0}".format(return_msg))


@client.event
async def on_ready():
    print('Logged in as')
    print(client.user.name)
    print(client.user.id)
    global issue_bot
    await issue_bot.checkRestarted()
    print('------')


@client.event
async def on_message(msg: discord.Message):
    await client.wait_until_ready()
    try:
        if msg.guild is None:
            return
        # exclude self posts
        if msg.author.id == issue_bot.client.user.id:
            return

        content = msg.content
        # only respond to the proper author
        if msg.author.id == issue_bot.config.root and content.startswith("!init"):
            args = content[len("!"):].split(' ')
            await issue_bot.initServer(msg, args[1:])
            return

        # only respond to the proper guilds
        guild_id_str = str(msg.guild.id)
        if guild_id_str not in issue_bot.config.servers:
            return

        server = issue_bot.config.servers[guild_id_str]
        prefix = server.prefix


        if msg.channel.id == server.chat:
            if not content.startswith(prefix):
                return
            args = content[len(prefix):].split(' ')
            base_arg = args[0].lower()

            authorized = await issue_bot.isAuthorized(msg.author, msg.guild)
            if base_arg == "help":
                await issue_bot.help(msg, args[1:])
            elif base_arg == "staffhelp":
                await issue_bot.staffhelp(msg, args[1:])
                # primary commands
            elif base_arg == "unresolved":
                await issue_bot.linkEarliestUnresolved(msg)
            elif base_arg == "update" and msg.author.id == issue_bot.config.root:
                await issue_bot.updateBot(msg)
            else:
                await msg.channel.send(msg.author.mention + " Unknown Command.")
        elif msg.channel.id == server.issue:
            if content.startswith(prefix):
                args = content[len(prefix):].split(' ')
                base_arg = args[0].lower()

                if msg.reference is not None:
                    authorized = await issue_bot.isAuthorized(msg.author, msg.guild)
                    if base_arg == "issue" and authorized:
                        await issue_bot.pushIssue(msg, [])
                    elif base_arg == "text" and authorized:
                        await issue_bot.pushIssue(msg, ["text"])
                    elif base_arg == "bug" and authorized:
                        await issue_bot.pushIssue(msg, ["bug"])
                    elif base_arg == "enhancement" and authorized:
                        await issue_bot.pushIssue(msg, ["enhancement"])
                    else:
                        await msg.add_reaction('\U0000274C')
            else:
                await issue_bot.beginIssue(msg)
        elif msg.channel.type == discord.ChannelType.public_thread:
            if msg.channel.parent.id == server.issue:
                reporter, prefix, current_msg = await issue_bot.getCurrentStep(msg.channel)
                if reporter:
                    await issue_bot.moveToNextStep(reporter, prefix, msg)



    except Exception as e:
        await issue_bot.sendError(traceback.format_exc())

@client.event
async def on_raw_reaction_add(payload):
    await client.wait_until_ready()

    try:

        if payload.user_id == client.user.id:
            return
        guild_id_str = str(payload.guild_id)
        msg = await client.get_channel(payload.channel_id).fetch_message(payload.message_id)
        server = issue_bot.config.servers[guild_id_str]
        if payload.channel_id == server.issue:
            # do not allow reacting if not authorized
            authorized = await issue_bot.isAuthorized(payload.member, msg.guild)
            if not authorized:
                await msg.remove_reaction(payload.emoji, payload.member)
        elif msg.channel.type == discord.ChannelType.public_thread:
            if msg.channel.parent.id == server.issue:
                reporter, prefix, current_msg = await issue_bot.getCurrentStep(msg.channel)
                if reporter:
                    if current_msg.id == msg.id:
                        await issue_bot.moveToNextStep(reporter, prefix, msg)
                    else:
                        await msg.remove_reaction(payload.emoji, payload.member)
                else:
                    await msg.remove_reaction(payload.emoji, payload.member)



    except Exception as e:
        await issue_bot.sendError(traceback.format_exc())

issue_bot = IssueBot(scdir, client)

with open(os.path.join(scdir, TOKEN_FILE_PATH)) as token_file:
    token = token_file.read()

try:
    client.run(token)
except Exception as e:
    trace = traceback.format_exc()
    print(trace)


if issue_bot.need_restart:
    # restart
    args = sys.argv[:]
    args.insert(0, sys.executable)
    if sys.platform == 'win32':
        args = ['"%s"' % arg for arg in args]

    os.execv(sys.executable, args)
