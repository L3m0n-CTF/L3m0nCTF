# 21. The participant verification portal (verify.l3m0nctf.xyz)

Every person who took part in L3m0n CTF 2026 gets one code, printed on their certificate with a QR code. Anyone (an employer, a college) can type
the code at **https://verify.l3m0nctf.xyz** or scan the QR code and see who the certificate was issued to.

| Code | Who | Shown on the page |
|---|---|---|
| `L3M0N26-ORG-xxxxx` | an organiser (the crew's list of names) | Event Name "L3m0n CTF 2026", Role "Organizer", Name, Issue Date |
| `L3M0N26-PAR-xxxxx` | a member of a studio that scored **above 0 TRP** in the online qualifiers | Event Name "L3m0n CTF 2026 Qualifiers", Role "Participant", Username, Studio Name, Issue Date |
| `L3M0N26-ACH-xxxxx` | a member of a **top-3** studio of the finals | Event Name "L3m0n CTF 2026 Finals", Role "Winner", Username, Studio Name, Studio Rank (1st-3rd), Issue Date |
| `L3M0N26-APP-xxxxx` | a member of any other finals studio | Event Name "L3m0n CTF 2026 Finals", Role "Participant", Username, Studio Name, Studio Rank (4th and up), Issue Date |

The five letters come from 21 consonants (no vowels: no I/O to mistake for 1/0, no accidental words) and are unique across all categories.
**One code per person:** a qualifier who reaches the finals ends with the finals code only (the qualifier code's page then says "replaced" and links to
the new one). How codes reach people: qualifiers see theirs on the platform's studio page (a later platform release, switched on by the organiser);
finalists get printed certificates.

## How it works (and why it can run for years)

The portal is a **static website**. `python -m l3mon verify build` turns the private credentials database into one ready-made page per code; nginx
serves the files read-only (`deploy/nginx/verify/verify.conf`, overlay `verify`). There is no application, no login and nothing that writes on the
server, the pages carry a strict Content-Security-Policy, and the same folder can later be served by any static host (a tiny VM, or a Cloudflare
Worker like the landing page) without changing a line. A withdrawn code's page names nobody.

Files: `tools/l3mon/verify/` (codes, database, issue rules, QR encoder, site builder, `export_prelims.py`), `tools/ops/verify-publish.sh`,
`deploy/nginx/verify/verify.conf`, `deploy/nginx/snippets/verify-headers.conf`, `deploy/compose/compose.verify.yml`; tests `tools/tests/test_verify_*.py`.

## PRIVATE data: never in this repository

- the **credentials database** (`credentials.sqlite`): the only record of the codes. Keep it on the owner's laptop **and** a backup copy (it cannot
  be re-made: codes are random). It holds real organiser names, usernames and studio names.
- the **organiser list** (one full name per line), the **prelims export**, the **finals results**, the **sponsor folder** (`sponsors.json` and logos).

Suggested place on the laptop: a folder outside every repository, e.g. `D:\l3m0nCTF\private-verify\` (and its backup).

## 1. Make the database (once)

```bash
cd tools
python -m l3mon verify init D:/l3m0nCTF/private-verify/credentials.sqlite
```

## 2. Organisers

```bash
python -m l3mon verify issue D:/l3m0nCTF/private-verify/credentials.sqlite --date 2026-11-30 --org D:/path/to/org.txt
```

Re-running with a longer list issues codes only to the new names; a removed name is reported, never deleted (use `withdraw`).

## 3. Qualifier participants (after the qualifiers end, 28 Nov 22:00 IST, and after any Revoke/Restore)

The export runs **read-only** inside the platform container on the VM (**ask the owner first**, rule R3):

```bash
# on the laptop: copy the export script up
# (the gcloud configuration and project as in the owner's private notes, with CLOUDSDK_CORE_PROJECT set as for tools/ops/gate.sh)
gcloud compute scp tools/l3mon/verify/export_prelims.py <platform VM>:/tmp/export_prelims.py --zone=<zone> --tunnel-through-iap --quiet
# on the VM
cd ~/L3m0nCTF && set -a && . /etc/l3mon/production.env && set +a
docker exec -i "$(tools/compose.sh ps -q ctfd)" bash -c "source /usr/local/lib/l3mon-env.sh; python -" < /tmp/export_prelims.py > /tmp/prelims-export.txt
# back on the laptop: fetch it, then DELETE both copies on the VM (rm /tmp/prelims-export.txt /tmp/export_prelims.py)
```

It lists every member of every studio the public scoreboard ranks (above 0 TRP; hidden and banned studios, banned or hidden accounts and crew
accounts are left out), between two marker lines. Then:

```bash
python -m l3mon verify issue D:/l3m0nCTF/private-verify/credentials.sqlite --date 2026-11-30 --prelims D:/path/to/prelims-export.txt
python -m l3mon verify list  D:/l3m0nCTF/private-verify/credentials.sqlite     # look before publishing
```

## 4. Finals (after 12 Dec)

The finals system's export format is **not decided yet**. Until it is, the tool reads a CSV with `username,studio,rank` and optionally `user_id`
(the platform's user id, which matches a qualifier exactly; without it the username is matched, ignoring case):

```bash
python -m l3mon verify issue D:/l3m0nCTF/private-verify/credentials.sqlite --date 2026-12-12 --finals D:/path/to/finals.csv
```

Ranks 1-3 become ACH (Winner), 4 and up APP; two studios cannot share a rank. The holders' qualifier codes are marked replaced in the same run.

**Who is who.** A player is recognised by either the platform user id or the username (Unicode-normalised, spaces and case ignored). When the
two point at two different people already in the database, the run stops and changes nothing: fix the input and run again.

## 5. Build and look

```bash
python -m l3mon verify build D:/l3m0nCTF/private-verify/credentials.sqlite D:/l3m0nCTF/private-verify/site --sponsors D:/l3m0nCTF/private-verify/sponsors
```

Pack it **in PowerShell** (Windows' own tar; Git Bash's GNU tar reads `D:/...` as a remote machine called "D"):

```powershell
tar -czf D:\l3m0nCTF\private-verify\verify-site.tgz -C D:\l3m0nCTF\private-verify\site .
```

`sponsors.json` is `[{"name": "CyberDefenders", "file": "cyberdefenders.png"}, ...]` in the order of the crawl; logos are PNG, SVG, WebP or JPEG
(300 KB at most; an SVG must be a plain drawing). The build refuses anything else and says why.

## 6. Publish (the VM: ask the owner first)

Once only, first: **deploy a release that contains the portal** (the usual release procedure, with its backup and fingerprint). The overlay file,
the nginx block, the `verify` rate-limit zone, the include in the production template and `tools/ops/verify-publish.sh` all arrive with it; on an
older checkout `tools/compose.sh` stops with "there is no overlay 'verify'".

Then make the folder and switch the overlay on (`/etc/l3mon/production.env`):

```bash
sudo mkdir -p /etc/l3mon/verify/site && sudo chown "$USER" /etc/l3mon/verify/site
# in /etc/l3mon/production.env: add ,verify to L3MON_OVERLAYS and a line L3MON_VERIFY_SITE_DIR=/etc/l3mon/verify/site
```

Every publish (copy the archive up with `gcloud compute scp`, then on the VM):

```bash
cd ~/L3m0nCTF && bash tools/ops/verify-publish.sh /tmp/verify-site.tgz && rm /tmp/verify-site.tgz
```

The first time only, after the overlay line: `set -a; . /etc/l3mon/production.env; set +a; (cd tools && python3 -m l3mon deploy check)` (the line
"verification portal" must be OK) and `tools/compose.sh up -d --wait nginx` (nginx is recreated with the block; a few seconds). Later publishes need
no restart: the script copies the files into the served folder.

**Cloudflare (the owner):** a DNS record `verify` -> the platform VM's address (the same address as `play`), **proxied** (orange cloud). An explicit
record wins over the instancer's wildcard `*`. The origin certificate already covers `*.l3m0nctf.xyz`, and Authenticated Origin Pulls applies to the
whole zone. **Access:** the portal must stay public. If the crew Access application ever carries the wildcard `*.l3m0nctf.xyz` (the strict instancer
set-up, docs/deploy/instancer.md), add a **Bypass** application for `verify.l3m0nctf.xyz`, exactly as for `play`: the most specific hostname wins.
Optional: a Cloudflare rate-limit rule for `verify.l3m0nctf.xyz` (nginx already allows 60 requests a minute per address).

## Check that it works

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://verify.l3m0nctf.xyz/   # 200 and no redirect (a redirect to cloudflareaccess.com means Access caught it)
curl -s -o /dev/null -w "%{http_code}\n" https://verify.l3m0nctf.xyz/L3M0N26-PAR-BBBBB  # 404 (no such code)
curl -s -D - -o /dev/null https://verify.l3m0nctf.xyz/<a real code> | grep -i "content-security-policy\|x-robots-tag"
```

Then open a real code in a browser and scan its QR code with a phone.

## Withdraw a code

```bash
python -m l3mon verify withdraw D:/l3m0nCTF/private-verify/credentials.sqlite L3M0N26-PAR-XXXXX --reason "why (kept private)"
```

Then build and publish again. The page then says the certificate was withdrawn, without any name. A routine re-run never gives that person a new
code. To correct a certificate (a wrong rank, a misspelt name), withdraw the old code and run `issue` with the corrected list and
`--reissue <old code>`: only then is a new code issued to that person.

## Moving the portal off this VM (before the VM is ever stopped)

The published folder is the whole website. Any static host that can map `/<CODE>` to `c/<CODE>.html`, answer unknown addresses with `404.html` and
send the headers of `deploy/nginx/snippets/verify-headers.conf` will do; the simplest is a small VM with nginx and `verify.conf` as it is.
