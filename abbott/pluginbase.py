from __future__ import print_function
import json
import UserDict
import os
import os.path
import sys

from twisted.internet import defer

class PluginConfig(UserDict.UserDict):
    """Installed in plugins as self.config. Provides a dictionary-like
    interface with a method .save() to save to persistent storage. Uses a json
    file as a backing store.

    """
    def __init__(self, jsonfile):
        """Initialize a config from a json file."""
        self._jsonfile = jsonfile
        with open(jsonfile, 'r') as inp:
            self.data = json.load(inp)

    def save(self):
        with open(self._jsonfile+"~", 'w') as out:
            json.dump(self.data, out, indent=4)
        os.rename(self._jsonfile+"~", self._jsonfile)


class PluginBoss(object):
    """Handles the loading and unloading of plugins and the reading 
    of config files and storage of configuration.

    There is one instance of this class per bot, and every plugin instance has
    a handle to it
    """
    def __init__(self, config, transport):
        self._configdir = config
        self._transport = transport

        self._filename = os.path.join(config, "config.json")

        self.loaded_plugins = {}

        if not os.path.exists(self._configdir):
            os.mkdir(self._configdir)
        elif not os.path.isdir(self._configdir):
            print("The config parameter should be a directory. Please make the necessary adjustments")
            sys.exit(1)
        
        try:
            self._load()
        except IOError:
            self._seed_defaults()

    def _seed_defaults(self):
        print("""\
It seems your config file doesn't exist or is unreadable.
I'll create a new one for you now""")
        server = raw_input("First, what irc server do you want to connect to? [irc.freenode.net] >")
        if not server.strip():
            server = "irc.freenode.net"
        prompt = "Uh huh, and what port would you like? SSL please. [7000] >"
        while True:
            port = raw_input(prompt)
            if not port.strip():
                port = 7000
            try:
                port = int(port)
            except ValueError:
                prompt = "That doesn't seem to be a number. I need a port /number/! [7000] >"
            else:
                break

        while True:
            admin = raw_input("And who should be the admin of this bot? This should be the nickserv account >")
            admin = admin.strip()
            if admin:
                break

        while True:
            nick = raw_input("What should the bot's nickname be? >")
            nick = nick.strip()
            if nick:
                break

        while True:
            channel = raw_input("Any particular channel I should join to begin with? >")
            channel = channel.strip()
            if channel:
                break


        print("Alright, I can take it from here. All other commands you can issue to the bot at runtime")

        self.config = {
                'core': {
                    'plugins': [
                        # Good default set of plugins to bootstrap functionality
                        'irc.IRCBotPlugin',
                        'irc.IRCController',
                        'ircutil.ReplyInserter',
                        # whois and names probably aren't necessary unless
                        # you're also running the ircadmin plugins, but just
                        # in case, it can't hurt.
                        'ircutil.IRCWhois',
                        'ircutil.Names',
                        'auth.Auth',
                        'plugincontroller.PluginController',
                        'corecontrol.CoreControl',
                        'corecontrol.Help',
                        ],
                    },
                'plugin_config': {
                    'irc.IRCBotPlugin': {
                        'server': server,
                        'port': port,
                        'nick': nick,
                        'channels': [channel],
                        # REMOVE is supported on freenode. I don't know about any others.
                        'remove': "freenode" in server,
                        },
                    'auth.Auth': {
                        'perms': {
                                admin: [
                                    [None, '*'] # all permissions on all channels
                                    ]
                            },
                        },
                    },
                'command': {
                    "prefix": None,
                    },
                }
        self.save()

    def _load(self):
        with open(self._filename, 'r') as file_handle:
            self.config = json.load(file_handle)

    def save(self):
        """Saves the master config. Use plugin.config.save() to save plugin
        configs
        
        """
        with open(self._filename, 'w') as output_file_handle:
            json.dump(self.config, output_file_handle, indent=4)

    def load_all_plugins(self):
        """Called by the main method at startup time to load all configured plugins"""
        for plugin_name in self.config['core']['plugins']:
            self.load_plugin(plugin_name)

    def load_plugin(self, plugin_name):
        """Loads the named plugin.
        
        plugin_name is expected to be in the form A.B where A is the module and
        B is the class. This module is expected to live in the plugins package.
        
        """
        modulename, classname = plugin_name.split(".")
        module = __import__("abbott.plugins."+modulename, fromlist=[classname])
        
        pluginclass = getattr(module, classname)
        
        plugin = pluginclass(plugin_name, self._transport, self)
        try:
            plugin.start()
        except Exception:
            self._transport.unhook_plugin(plugin)
            raise

        self.loaded_plugins[plugin_name] = plugin

    def unload_plugin(self, plugin_name):
        plugin = self.loaded_plugins.pop(plugin_name)
        self._transport.unhook_plugin(plugin)
        plugin.stop()

    def get_plugin_config(self, plugin_name):
        """Returns a config dictionary for the named plugin. This dict has an
        additional method: .save(), to save any changes back to persistant
        store

        """
        try:
            old_config = self.config['plugin_config'][plugin_name]
        except KeyError:
            old_config = {}
        
        plugin_config_path = os.path.join(self._configdir, plugin_name)+".json"

        if not os.path.exists(plugin_config_path):
            with open(plugin_config_path, "w") as out:
                json.dump(old_config, out, indent=4)

        if "plugin_config" in self.config and plugin_name in self.config['plugin_config']:
            del self.config['plugin_config'][plugin_name]
            self.save()
        if "plugin_config" in self.config and not self.config['plugin_config']:
            del self.config['plugin_config']
            self.save()


        return PluginConfig(plugin_config_path)


class BotPlugin(object):
    """All bot plugins should inherit from this. It provides methods for
    talking to the transport layer and for saving persistent configuration

    """
    def __init__(self, plugin_name, transport, pluginboss):
        self.plugin_name = plugin_name
        self.transport = transport
        self.pluginboss = pluginboss

        self.reload()

    ### Plugins should override these methods if appropriate

    def reload(self):
        """This is called to indicate the configuration has changed and the
        plugin should make any necessary changes to its runtime.
        
        This method is called by the constructor, and anytime an external event
        indicates the configuration has changed.

        Feel free to override. This is just an example.

        """
        self.config = self.pluginboss.get_plugin_config(self.plugin_name)

    def start(self):
        """Do any initialization here. This is called after __init__()

        This should do any sort of interaction with the twisted reactor such as connecting

        """
        pass

    def stop(self):
        """Do any finilization here. This should unhook any events it has
        hooked and remove it from the twisted reactor if applicable

        """
        pass

    ### Convenience dispatcher methods, but feel free to override them if you
    ### want!

    def received_event(self, event):
        """An event has been received by this plugin"""
        method = getattr(self, "on_event_%s" % event.eventtype.replace(".","_"), None)
        if method:
            method(event)

    def received_middleware_event(self, event):
        """This event has been intercepted before it got to its destination. We
        can return a new / modified event, or None to indicate the event should
        be swallowed

        """
        method = getattr(self, "on_middleware_%s" % event.eventtype.replace(".","_"), None)
        if method:
            return method(event)
        return event

    def incoming_request(self, name, *args, **kwargs):
        """A request has been issued to this plugin. Return a deferred.

        """
        method = getattr(self, "on_request_%s" % name.replace(".","_"), None)
        if method:
            toret = method(*args, **kwargs)
        else:
            toret = defer.fail(NotImplementedError("The plugin does not provide that request method"))
        return toret

    ### Convenience methods for use by the plugin to install event listeners
    def install_middleware(self, matchstr):
        self.transport.install_middleware(matchstr, self)

    def listen_for_event(self, matchstr):
        self.transport.listen_for_event(matchstr, self)

    def provides_request(self, name):
        self.transport.provides_request(name, self)

