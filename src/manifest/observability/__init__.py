"""Telemetry — spans, and the cost meter that says modelled.

Outside `core/` because a span carries a clock and the core may not read one. The core produces
the *facts*; this turns them into something a dashboard can hold, and it is the only place in
the system where a wall-clock reading is allowed.
"""
