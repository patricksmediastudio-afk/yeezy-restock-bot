#!/usr/bin/env python3
"""Tests for the alert rule and the seen-state file.

Plain stdlib, no pytest, so it runs anywhere the bot runs:

    python test_matching.py

The headline corpus below is the point of this file. The bot watches for a
one-shot event, so the expensive failure is a drop headline that does not
alert. Every SHOULD_FIRE entry is a phrasing sneaker sites actually use.
"""

import json
import os
import sys
import tempfile

# Must be set before importing the bot: STATE_FILE is resolved at import time.
_TMP = tempfile.mkdtemp(prefix="restock-test-")
os.environ["STATE_DIR"] = _TMP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import restock_bot as rb  # noqa: E402


# Headlines that must alert. Missing one of these means missing the drop.
SHOULD_FIRE = [
    "Yeezy YS-01 Slides Releasing Online At JD Sports This Friday",
    "Where To Buy The Yeezy Slide YS-01 Online",
    "JD Sports Yeezy Slides Restock Store List",
    "Yeezy YS-01 Slide Online Raffle Now Open At JD Sports",
    "Yeezy Slides Return To JD Sports Website",
    "The Yeezy YS-01 Is Coming Back In Stock",
    "Yeezy YS-01 Slide Sandals Now Live At Finish Line",
    "How To Cop The Yeezy Slide Online",
    "Yeezy YS-01 Slide Restocking Next Week",
    "Adidas Yeezy Slide Onyx Restock Confirmed",
    "Yeezy Slide Sandals Drop Date Announced",
    "YS-01 Slides Hit JD Sports Online Tomorrow",
    "Yeezy Slide YS-01 Goes Live At 9am",
    "The Yeezy YS-01 Slide Is Available Now",
    "Yeezy Slides Sold Out In Minutes At JD",
]

# Headlines that must not alert. A Yeezy that is not a slide, or a slide that
# is not a Yeezy, is noise.
SHOULD_NOT_FIRE = [
    "Nike Dunk Low Restock Coming Soon",
    "Yeezy 700 V3 Release Date Confirmed",
    "Yasiin Bey Wore New Yeezy Boots At The Roots Picnic",
    "New Yeezy 800 Colorways August 2026",
    "Adidas Slide Restock At Foot Locker",
    "Yeezy Foam Runner Release Date",
    "Every Yeezy Sneaker Releasing At JD Sports August 2026",
    "Nike Calm Slide Drops In Three New Colors",
]

# Headline -> the tier its reason string must carry.
TIERS = [
    ("JD Sports Yeezy Slides Restock Store List", "RESTOCK"),
    ("Yeezy YS-01 Slides Releasing Online At JD Sports", "availability"),
    ("Adidas Yeezy Slide Vs Yeezy Slide YS-01", "mention"),
]

# Headlines that must be labelled as naming the retailer Patrick buys from.
SHOULD_SAY_JD = [
    "JD Sports Yeezy Slides Restock Store List",
    "Yeezy YS-01 Slide Sandals Now Live At Finish Line",
]


def check(failures, condition, message):
    if not condition:
        failures.append(message)


def test_should_fire(failures):
    for title in SHOULD_FIRE:
        ok, _ = rb.score_item(title)
        check(failures, ok, "MISSED a drop headline: {!r}".format(title))


def test_should_not_fire(failures):
    for title in SHOULD_NOT_FIRE:
        ok, reason = rb.score_item(title)
        check(failures, not ok,
              "false positive on {!r} (reason: {})".format(title, reason))


def test_tiers(failures):
    for title, expected in TIERS:
        ok, reason = rb.score_item(title)
        check(failures, ok, "tier case did not fire at all: {!r}".format(title))
        check(failures, expected in reason,
              "{!r} got tier {!r}, expected {!r}".format(title, reason, expected))


def test_jd_label(failures):
    for title in SHOULD_SAY_JD:
        _, reason = rb.score_item(title)
        check(failures, "JD SPORTS" in reason,
              "{!r} was not labelled JD SPORTS (got {!r})".format(title, reason))


def test_releasing_regression(failures):
    """The specific bug: "release" does not match "releasing"."""
    ok, _ = rb.score_item("Yeezy Slides Releasing At JD Sports")
    check(failures, ok, '"Releasing" must be treated as availability language')


def test_seen_state_trims_oldest_first(failures):
    """The seen file must drop its oldest keys, not an arbitrary selection.

    A plain set would fail this: iteration order is arbitrary, so an
    already-alerted article could survive a trim while a newer one is
    discarded, and then re-alert on the next run.
    """
    seen = rb.load_seen()
    check(failures, seen == {}, "expected empty state in a fresh temp dir")

    for i in range(600):
        rb.mark_seen(seen, "key-{:04d}".format(i))
    rb.save_seen(seen)

    written = json.loads(rb.STATE_FILE.read_text(encoding="utf-8"))
    check(failures, len(written) == 500,
          "expected 500 keys after trim, got {}".format(len(written)))
    check(failures, written[0] == "key-0100",
          "oldest surviving key should be key-0100, got {}".format(written[0]))
    check(failures, written[-1] == "key-0599",
          "newest key should be key-0599, got {}".format(written[-1]))

    # And it must survive a round trip in the same order.
    check(failures, list(rb.load_seen()) == written,
          "load_seen did not preserve the order it was written in")


def test_seen_membership(failures):
    seen = rb.load_seen()
    check(failures, "key-0599" in seen, "membership test broken on reload")
    check(failures, "key-0000" not in seen, "trimmed key should not be present")


def test_subject_lines(failures):
    """The subject line is what shows on a lock screen, so it carries the tier.

    Sends nothing: send_email is swapped for a recorder first.
    """
    captured = []
    real_send = rb.send_email
    rb.send_email = lambda subject, body: (captured.append(subject), True)[1]
    try:
        cases = [
            ("JD Sports Yeezy Slides Restock Store List", "JD RESTOCK ALERT: "),
            ("Yeezy YS-01 Slide Restock Confirmed", "RESTOCK ALERT: "),
            ("Yeezy Slides Releasing At JD Sports", "JD Yeezy slide availability: "),
            ("Yeezy Slide YS-01 Drops Friday", "Yeezy slide availability: "),
            ("Adidas Yeezy Slide Vs Yeezy Slide YS-01", "Yeezy slide mention: "),
        ]
        for title, expected_prefix in cases:
            ok, reason = rb.score_item(title)
            check(failures, ok, "subject case did not fire: {!r}".format(title))
            if not ok:
                continue
            item = {"title": title, "link": "https://example.test/x",
                    "summary": "", "source": "test"}
            rb.alert(item, reason)
            subject = captured[-1]
            check(failures, subject.startswith(expected_prefix),
                  "{!r} produced subject {!r}, expected prefix {!r}".format(
                      title, subject, expected_prefix))
    finally:
        rb.send_email = real_send


def main():
    failures = []
    tests = [
        test_should_fire,
        test_should_not_fire,
        test_tiers,
        test_jd_label,
        test_releasing_regression,
        test_seen_state_trims_oldest_first,
        test_seen_membership,
        test_subject_lines,
    ]
    for test in tests:
        before = len(failures)
        test(failures)
        status = "ok  " if len(failures) == before else "FAIL"
        print("  {} {}".format(status, test.__name__))

    print()
    if failures:
        print("{} failure(s):".format(len(failures)))
        for f in failures:
            print("  - {}".format(f))
        return 1
    print("All checks passed. {} drop headlines fire, {} noise headlines ignored.".format(
        len(SHOULD_FIRE), len(SHOULD_NOT_FIRE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
