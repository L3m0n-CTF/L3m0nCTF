# The beginner site (the Beginners Section of 26 Nov 2026): a small Flask app with 15 guided challenges (lessons, practice files, live labs).
#
# Its SOURCE IS NOT IN THIS REPOSITORY: it holds every flag, and this repository is public. The source comes from the private repository (beginner/) or the author's zip:
#   bash tools/ops/beginner.sh pack <the folder with app.py> <out.tgz>     (copies ONLY app.py, challenges.py, templates/ and static/; no flags file, no tests, no build leftovers)
#   mkdir src && tar -xzf out.tgz -C src
#   docker build -f deploy/beginner/Dockerfile -t l3mon/beginner:<tag> src
#
# `pack` adds two small files of ours to the archive (guard.py and serve.py: a time and size limit on the SQL labs, see guard.py); the author's files are not edited.
# The image never contains the author's flags/ folder, tests, build files or compiled leftovers: only the things listed in the COPY lines can get in, whatever else the
# folder holds. It runs as an unprivileged user with nothing writable (the compose overlay makes the root file system read-only), and listens on 8000 inside its own network.
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# exact versions (resolved in this base image on 26 Nov 2026), nothing else from the author's requirements.txt: the rest of it only builds the practice files
RUN pip install --no-cache-dir --disable-pip-version-check \
      flask==3.1.3 werkzeug==3.1.9 jinja2==3.1.6 markupsafe==3.0.4 itsdangerous==2.2.0 click==8.5.1 blinker==1.9.0 waitress==3.0.2 \
 && groupadd -g 10003 beginner \
 && useradd -u 10003 -g beginner -M -s /usr/sbin/nologin beginner

WORKDIR /app
COPY --chown=root:root app.py challenges.py serve.py guard.py /app/
COPY --chown=root:root templates /app/templates
COPY --chown=root:root static /app/static
RUN chmod -R a=rX /app

USER 10003:10003
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=4s --retries=5 --start-period=10s \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/robots.txt', timeout=3).status == 200 else 1)"]

CMD ["python", "serve.py"]
