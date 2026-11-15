"""A flag attempt must be text of a sensible length (found by the end-to-end test of 15 November).

CTFd's attempt route takes whatever the request holds under `submission`: a missing value, a number, a list or `null` ended in a server
error (500), and a text of any length was stored and written to the log. The route is asked before CTFd's handler runs:

- not text (JSON only; a form always carries text) -> 400, error `invalid_submission`;
- longer than MAX_FLAG_LENGTH characters -> 400, the same error.

Flags of this platform are `L3m0n{...}` of a few dozen characters; the limit leaves room for a far longer one and still keeps a pasted
page out of the submissions table.
"""
from flask import jsonify, request

MAX_FLAG_LENGTH = 512
ENDPOINT = "api.challenges_challenge_attempt"


def refuse(message):
    response = jsonify({"success": False, "error": "invalid_submission", "message": message})
    response.status_code = 400
    return response


def check_the_attempt():
    if request.method != "POST" or request.endpoint != ENDPOINT:
        return None
    # the same test CTFd's handler makes to choose where it reads the body from
    if request.content_type != "application/json":
        value = request.form.get("submission", "")
    else:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return refuse("Send the flag as text.")
        value = body.get("submission")
        if not isinstance(value, str):
            return refuse("Send the flag as text.")
    if len(value) > MAX_FLAG_LENGTH:
        return refuse(f"That is too long to be a flag (at most {MAX_FLAG_LENGTH} characters).")
    return None


def install(app) -> None:
    if "l3mon_attempt_input" in app.extensions:
        return
    app.extensions["l3mon_attempt_input"] = True
    app.before_request(check_the_attempt)
