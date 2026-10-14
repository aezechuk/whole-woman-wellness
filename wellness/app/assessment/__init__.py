from flask import Blueprint

bp = Blueprint("assessment", __name__)

from . import routes  # noqa: E402,F401
