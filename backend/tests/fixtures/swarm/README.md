Swarm collector test fixtures. Field sets and nesting come from real responses recorded on 2026-11-14
(Bluesky public AppView getPostThread/getQuotes, Mastodon /api/v1/statuses + /context); every handle, DID,
text, URL and time was replaced, and a copy-paste campaign was planted so tests exercise detection.
YouTube responses are built in the tests from the documented Data API v3 shapes (no key was available
to record). No live network is used in tests.
