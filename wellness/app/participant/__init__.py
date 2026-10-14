from flask import Blueprint

bp = Blueprint("participant", __name__)

from . import routes  # noqa: E402,F401
