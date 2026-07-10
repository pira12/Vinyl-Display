# Contributing

Thanks for your interest in Vinyl Display. Bug reports, feature ideas, and pull
requests are all welcome.

## Getting set up

The backend is Python and the frontend is a React and Vite app. The setup script
installs system dependencies, builds Olaf, and creates a virtualenv:

```bash
./scripts/setup_pi.sh
```

Run the backend with the mock recognizer so you do not need any hardware:

```bash
./.venv/bin/python -m backend.main --simulate
```

In another shell, run the frontend dev server. It proxies `/api`, `/ws`, and
`/art` to the backend on port 8080:

```bash
cd frontend
npm install
npm run dev
```

Microphone capture only works over HTTPS or localhost, so test mic features
against the localhost dev server or a deployed HTTPS host.

## Running the tests

```bash
./.venv/bin/python -m pytest     # backend tests
cd frontend && npm run build     # frontend build check
```

Please make sure both pass before opening a pull request.

## Pull requests

- Keep changes focused. One topic per pull request is easier to review.
- Match the style of the surrounding code.
- Add or update tests when you change backend behavior.
- Describe what the change does and why in the pull request body.

## Reporting bugs

Open an issue with steps to reproduce, what you expected, and what happened.
Deploy problems, recognition accuracy, and lyric sync issues are all fair game.
Include your host type (Raspberry Pi, NAS, Docker on x86) and the recognition
backend you are using, since those often matter.
