# Android signing setup (Fix 6a) — the three commands

This is the whole checklist for generating your Android signing key and
getting the SHA-1 fingerprint that Google OAuth needs.  You do not need
to understand what any of it means — copy each block, paste it into the
terminal on your laptop, and follow the prompts.

**Time:** about 15 minutes.  **Cost:** zero.

---

## Before you start — one-time laptop setup

You need two things on your laptop.  If you already have them, skip.

### 1. Node.js (which comes with npm)

- **Mac:** open **https://nodejs.org/en/download** and click the big
  "LTS" download button.  Run the installer.  Click Next through everything.
- **Windows:** same link, same button.  Run the installer.  Click Next
  through everything.  When it asks about "Automatically install the
  necessary tools", leave the box **unchecked** — you don't need it.

Verify it worked.  Open Terminal (Mac: press ⌘+Space, type "Terminal",
enter) or PowerShell (Windows: press ⊞+X, click "Terminal") and paste:

```
node --version
```

You should see something like `v22.11.0`.  If you see "command not
found", close the terminal, open a new one, try again.

### 2. The EAS CLI (Expo's build tool)

In that same terminal, paste:

```
npm install -g eas-cli
```

Wait a minute.  When it finishes, paste:

```
eas --version
```

You should see a version number.

---

## Command 1 — Log into your Expo account

```
eas login
```

It will ask for your **Expo username or email** and **password**.  Use
the account you made at https://expo.dev/ earlier.  If you haven't yet,
go make it now — it's free.

You'll only ever have to do this once per laptop.

---

## Command 2 — Link this repository to your Expo account

Open a terminal and navigate into the `frontend` folder of the GlamGenius
repository you cloned from GitHub.  If you don't remember cloning it,
just click this once in Terminal (paste the whole thing):

```
cd Desktop && git clone https://github.com/blazebrt/GlamGenius.git && cd GlamGenius/frontend
```

(If the folder already exists it will complain — just do `cd GlamGenius/frontend`.)

Now, still in that folder, paste:

```
eas init
```

It will ask **"Would you like to create a new project?"** — answer **yes**.
It will pick a slug automatically (`glamgenius`).  When it finishes it
will have written a `projectId` into `app.json`.

---

## Command 3 — Generate the signing key and read out the SHA-1

Paste:

```
eas credentials
```

A menu appears.  Use arrow keys and Enter:

1. **Select platform:** `Android`
2. **Select build profile:** `production`
3. **What do you want to do?** → **"Keystore: Manage everything needed to build your project"**
4. **Keystore configuration:** → **"Set up a new keystore"**
5. When it asks **"Generate a new Android Keystore?"** → **yes**.
6. Confirm any prompts with the default answer.

You'll now see a printed block that looks like this:

```
Android Keystore

Type                  JKS
Key Alias             xxxxxx
MD5 Fingerprint       AA:BB:CC:DD:...
SHA1 Fingerprint      12:34:56:78:9A:BC:DE:F0:...          ← THIS ONE
SHA256 Fingerprint    ...
Updated               just now
```

**Copy the SHA1 Fingerprint line.**  That's what Google Cloud needs for
your OAuth client.  Paste it back to me here.  Also send me the
`projectId` that got written into `app.json` (open the file in any text
editor — Notepad, TextEdit — and copy the whole line that has
"projectId" in it).

---

## That's it

You now have:
- A signing key stored securely with Expo (Expo keeps a backup — you
  cannot lose it as long as your Expo account is intact).
- A SHA-1 fingerprint to paste into Google Cloud.
- A `projectId` linking this repository to your Expo account.

Once you paste those two things back to me, I can finish Fix 6 (real
weather + calendar + push) without asking you for anything else.

---

## If something goes wrong

- **"You are not authorized to perform this action"** — you're logged
  into the wrong Expo account, or the account doesn't own the slug
  `glamgenius`.  Run `eas whoami` to see which account you're on, and
  `eas logout` + `eas login` to switch.
- **"Missing app.json / expo config"** — you're not in the `frontend`
  folder.  Run `pwd` (Mac/Linux) or `cd` (Windows) to see where you
  are; you should see the path end in `/GlamGenius/frontend`.
- **"There was an error running the pod install"** — ignore, it's an
  iOS thing you don't need.
- **Anything else** — screenshot the error and send it to me.

## What this does NOT do

- It does not upload your app to the Play Store — that's a separate
  step later.
- It does not cost anything.
- It does not put your signing key in your git repository (that would
  be a security disaster; Expo keeps it, not your code).
