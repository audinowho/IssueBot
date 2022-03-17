import os
import io
import discord
import urllib
import traceback
import asyncio
import json
import requests
import git
import sys
import re
import IssueUtils


# Housekeeping for login information
TOKEN_FILE_PATH = 'token.txt'
CONFIG_FILE_PATH = 'config.json'
PRIVATE_KEY_FILE_PATH = 'private-key.pem'

scdir = os.path.dirname(os.path.abspath(__file__))

# The Discord client.
client = discord.Client()

class BotServer:

    def __init__(self, main_dict=None):
        self.issue = 0
        self.chat = 0
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
    A class for handling recolors
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

    async def pushIssue(self, msg):
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

        url = IssueUtils.create_issue(header, self.config.repo_owner, self.config.repo_name, title, body)
        # react with a star... and a reply?
        await issue_msg.add_reaction('\U000021A9')


    async def checkAllSubmissions(self):

        # make sure they are re-added
        for server in self.config.servers:
            ch_id = self.config.servers[server].issue
            msgs = []
            channel = self.client.get_channel(ch_id)
            async for message in channel.history(limit=None):
                msgs.append(message)
            for msg in msgs:
                try:
                    await self.pollSubmission(msg)
                except Exception as e:
                    await self.sendError(traceback.format_exc())


    async def pollSubmission(self, msg):
        # check for messages in #submissions

        cks = None
        ss = None
        remove_users = []
        for reaction in msg.reactions:
            if reaction.emoji == '\u2705':
                cks = reaction
            elif reaction.emoji == '\u2B50':
                ss = reaction
            else:
                async for user in reaction.users():
                    remove_users.append((reaction, user))

        auto = False
        if cks:
            async for user in cks.users():
                if user.id == self.config.root:
                    auto = True
                else:
                    remove_users.append((cks, user))
        if ss:
            async for user in ss.users():
                if user.id != self.client.user.id:
                    pass
                else:
                    remove_users.append((ss, user))

        for reaction, user in remove_users:
            await reaction.remove(user)

        if auto:
            await self.pushIssue(msg)


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
    # await issue_bot.checkAllSubmissions()
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

        if not content.startswith(prefix):
            return
        args = content[len(prefix):].split(' ')
        base_arg = args[0].lower()

        if msg.channel.id == server.chat:
            authorized = await issue_bot.isAuthorized(msg.author, msg.guild)
            if base_arg == "help":
                await issue_bot.help(msg, args[1:])
            elif base_arg == "staffhelp":
                await issue_bot.staffhelp(msg, args[1:])
                # primary commands
            elif base_arg == "update" and msg.author.id == issue_bot.config.root:
                await issue_bot.updateBot(msg)
            else:
                await msg.channel.send(msg.author.mention + " Unknown Command.")
        elif msg.reference is not None:
            authorized = await issue_bot.isAuthorized(msg.author, msg.guild)
            if base_arg == "issue" and authorized:
                await issue_bot.pushIssue(msg)
            else:
                await msg.add_reaction('\U0000274C')


    except Exception as e:
        await issue_bot.sendError(traceback.format_exc())

@client.event
async def on_raw_reaction_add(payload):
    await client.wait_until_ready()
    try:
        return

        if payload.user_id == client.user.id:
            return
        guild_id_str = str(payload.guild_id)
        if payload.channel_id == issue_bot.config.servers[guild_id_str].issue:
            msg = await client.get_channel(payload.channel_id).fetch_message(payload.message_id)
            await issue_bot.pollSubmission(msg)

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
