# Security

## Security model

Chattera is a **single-user application that runs on your own computer**.

- The server listens on `127.0.0.1` only and refuses any other address. It has no user accounts.
- Every API call and the WebSocket need a random session token that is created at start-up and injected only into the
  page served to your own browser. Requests from other origins and other host names are rejected.
- API keys are stored in the `keys` folder of the data directory (restricted to your Windows account), are never sent
  back to the browser and are redacted from logs.
- Models cannot run commands or write files. API replies have no such tools; subscription-CLI replies run in an empty
  sandbox folder with file-writing and shell tools switched off.
- Model output, web pages and documents are treated as untrusted text. Documents are parsed in a separate,
  time-limited process.

Out of scope: exposing Chattera to a network (unsupported), other software on the same computer acting as you, and the
security of the AI providers you connect to.

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead: the repository's **Security** tab →
**Report a vulnerability**. Include the version, what an attacker could do, and steps to reproduce. Do not include real
API keys or private chat content.

You can expect an answer within two weeks. This is a personal open-source project, so there is no bug bounty.
