import os
plugin_paths = [os.environ["SMOKE_HOME"], os.path.join(os.environ["SMOKE_PKG"], "python", "plugins")]
home_plugin_path = os.environ["SMOKE_HOME"]
available_plugins = []
active_plugins = []
def loadPlugin(k): return True
def startPlugin(k): active_plugins.append(k); return True
