# Authentication

Until this existed every request resolved to a single default operator. That was
a seam, not a security model, and this is the body that fills it. Four decisions
shape it, and each of them costs something.

## Sessions are opaque and server-side

A self-contained token is cheaper to check and impossible to withdraw: it stays
valid until it expires, whatever anyone decides in the meantime. That is the
wrong trade for a product whose central discipline is that an emergency stop
takes effect at once. Every request costs one indexed read, and in exchange a
session ends the moment somebody says so.

Only the SHA-256 of the token is stored. A database that leaks must not hand the
reader a set of live sessions. A fast hash is correct **here and only here**: the
token is 256 bits from the system CSPRNG, so there is no guessable input for a
slow hash to protect. Passwords, which are guessable, go through PBKDF2-SHA256
at 240,000 iterations.

Sessions have both an absolute lifetime and an idle timeout. The idle limit is
the one that matters day to day: a browser left open on a machine somebody
walked away from should stop being a way in. A session that times out is
**revoked**, not merely refused, so the next attempt does not depend on the same
clock arithmetic being repeated correctly.

## Failures are counted against the account

An attacker chooses their address and cannot choose the account, so counting
failures per address is a lock whose key the attacker holds. Eight consecutive
failures lock an account for fifteen minutes, and the correct password is
refused during the lockout too: a lock the right password opens is not a lock
against somebody about to find it.

The cost is that anyone who can guess an email can lock its owner out for a
quarter of an hour. That is the lesser harm, and it is why the lockout is
minutes rather than permanent.

## The failure message never says which half was wrong

"No such account" and "wrong password" are the same sentence, and the same work
is done either way — an unknown address still pays for a password hash. A login
that returns faster for an address with no account is an endpoint that answers
"does this person have an account" to an anonymous caller.

## There is no self-registration

Accounts are created by an owner, or from the command line:

```bash
python -m scripts.create_user --email you@example.com
```

The password is read from a prompt rather than an argument, because an argument
is in the shell history and in the process list. `--reset` sets a new password on
an existing account and ends every session it had.

Accounts that predate authentication have no password hash, and `authenticate`
refuses those: the old default operator is not a way in.

## Two credentials, one session

A browser sends the session cookie — HttpOnly, SameSite=Lax, host-only, and
Secure everywhere but plain-HTTP local development. A script sends the same
token as `Authorization: Bearer`. Both resolve through the same function against
the same stored row, so a session revoked for one is revoked for both.

## The web app and the API must be same-site

This is a constraint of cookie authentication with server-rendered pages, not a
preference. Every page renders on the server, so the Next server has to be able
to read the cookie the browser holds; a cookie set on another site is one it can
never see. The symptom when they disagree is unhelpfully silent — login returns
200 and the app bounces straight back to the sign-in screen — so two things
address it:

* Loopback aliases are reconciled in the browser. `localhost` and `127.0.0.1`
  are the same machine and different cookie hosts, and that mismatch is almost
  always an environment variable somebody else set. Only loopback is touched.
* After a successful login the form asks the API who it is. If the answer is
  "nobody", it says plainly that the session was discarded and names both hosts,
  rather than bouncing with no explanation.

`SECURE_COOKIES` must be true anywhere served over HTTPS. Left true on a
plain-HTTP deployment, the browser accepts the login response and drops the
cookie.

## Where the boundary is

`get_auth` in `api/deps.py` is the only place identity is decided, and every
scoped query reads `organization_id` off the context it returns. No route
resolves a tenant its own way.

In the interface the check lives in the layout shared by every page behind the
sign-in, so a page cannot be added without it. The middleware turns away a
request carrying no cookie before anything renders; the layout catches the
cookie that exists and is no longer good, which the middleware cannot tell apart
from one that is.

## What is deliberately absent

No password reset by email, which is a second way in maintained for the rarer
case. No OAuth. No API keys separate from sessions: the scheduler runs in
process and never crosses the HTTP boundary, and shipping an unused credential
type is worse than not having one. No roles beyond owner and operator, because
only one permission currently differs between them.
