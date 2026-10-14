from flask import Blueprint

bp = Blueprint("reflect", __name__)

from . import routes  # noqa: E402,F401
