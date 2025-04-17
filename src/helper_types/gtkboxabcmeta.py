from abc import ABCMeta, abstractmethod
from gi.repository import Gtk

# Abstract class that must inherit Gtk.Box
class GtkBoxABCMeta(type(Gtk.Box), ABCMeta):
    pass
