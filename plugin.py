# -*- coding: utf-8 -*-
from Plugins.Plugin import PluginDescriptor
from .screens.SourceSelector import SourceSelector
from .utils.player import start_proxy

def main(session, **kwargs):
    # Start the proxy server in background (if not already started)
    start_proxy()
    session.open(SourceSelector)

def Plugins(**kwargs):
    return [
        PluginDescriptor(
            name="MultiMovie",
            description="Browse movies/series from multiple Arabic sites",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon="plugin.png",
            fnc=main
        )
    ]