"""l3mon_core: the base plugin of the L3m0nCTF platform.

Today it does three small things: it answers a health request so the stack can prove the plugin is loaded, it sends the
bare address of the platform to the right place, and it can switch the session cookie to its strict production form.
Later sub-projects add the board and scoreboard data (SP3) here.
"""
import os

from flask import Blueprint, jsonify, redirect, request

l3mon = Blueprint("l3mon_core", __name__)


@l3mon.route("/l3mon/healthz")
def healthz():
    # No database access: this must keep answering when everything else is struggling.
    response = jsonify({"status": "ok", "plugin": "l3mon_core"})
    response.headers["Cache-Control"] = "no-store"
    return response, 200


def _switched_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def load(app):
    from CTFd.plugins.l3mon_core.versions import check_ctfd_version

    check_ctfd_version()  # first of all: on a CTFd we were not built for no l3mon plugin loads (CTFd has already migrated its own tables by now)

    from CTFd.plugins.l3mon_core import models  # noqa: F401  (registers the five tables)
    from CTFd.plugins.l3mon_core.tick import install as install_tick
    from CTFd.plugins.migrations import upgrade

    upgrade(plugin_name="l3mon_core")  # MariaDB: the migration; SQLite: create_all, like CTFd's own plugins

    from CTFd.plugins.l3mon_core.attempt_input import install as install_attempt_input
    from CTFd.plugins.l3mon_core.hidden_admins import install as install_hidden_admins
    from CTFd.plugins.l3mon_core.log_redaction import install as install_log_redaction
    from CTFd.plugins.l3mon_core.player_locks import install as install_player_locks

    install_tick(app)  # every committed change a player can see moves the tick once
    install_hidden_admins()  # the crew's accounts are never listed to players
    install_attempt_input(app)  # a flag attempt that is not text, or is far too long, is answered 400 (it was a server error)
    install_log_redaction()  # a submitted flag is not written to the application log
    install_player_locks(app)  # joining a studio and unlocking a hint wait for each other (the studio's row is locked first)

    from CTFd.plugins.l3mon_core import activity

    activity.install()  # the activity ledger: studio changes are seen wherever they come from, and a line follows the commit that made its action real

    from CTFd.plugins.l3mon_core import studio_rules

    studio_rules.install(app)  # a removed member cannot rejoin the studio; the rename policy on the API path (after player_locks, so the studio's row lock is held)

    from CTFd.plugins.l3mon_core.crew_api import bp as crew_activity_api

    app.register_blueprint(crew_activity_api)  # the crew's removals list, lift, and the ledger (administrator surface)
    app.register_blueprint(l3mon)

    # The crew's Config page offers MajorLeagueCyber, which this event does not use: a stylesheet keeps its tab and its choice out of sight (the settings
    # themselves are pinned empty in the preset).
    from CTFd.plugins import register_admin_plugin_stylesheet, register_plugin_assets_directory

    register_plugin_assets_directory(app, base_path="/plugins/l3mon_core/assets/")
    register_admin_plugin_stylesheet("/plugins/l3mon_core/assets/admin-tidy.css")

    # Production runs behind HTTPS only. The __Host- prefix makes browsers refuse the cookie unless it is Secure, has
    # Path=/ and has no Domain, so it can never be set from a sibling sub-domain or sent over plain HTTP.
    if _switched_on("L3MON_SECURE_COOKIES"):
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_NAME"] = "__Host-session"

    @app.before_request
    def send_the_root_to_the_right_place():
        # The platform has no home page of its own: the landing page is a separate site.
        if request.path == "/":
            from CTFd.utils.user import authed

            return redirect("/challenges" if authed() else "/login", code=303)
        return None
