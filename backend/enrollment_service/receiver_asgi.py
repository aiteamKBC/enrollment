"""Run with Daphne and a separately supplied receiver environment."""
from asgiref.wsgi import WsgiToAsgi
from .lms_receiver import application as receiver
application = WsgiToAsgi(receiver)
