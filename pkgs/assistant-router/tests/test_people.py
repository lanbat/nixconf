from assistant_router.people import People

NAMES = {"kiril": "Kiril", "maria": "Maria", "ivo": "Ivo"}


def scores(**s):
    base = {k: 0.1 for k in NAMES}
    base.update(s)
    return base


def test_nothing_known_says_nothing():
    p = People(NAMES)
    assert p.line("Kitchen", now=0.0) is None
    assert p.assess("Kitchen", now=0.0).speaker is None


def test_a_clear_voice_names_the_speaker():
    line = People(NAMES).line("Kitchen", now=0.0, voice=scores(kiril=0.75))
    assert "talking to Kiril" in line and "by voice" in line
    assert "never" in line  # the hint is never authority


def test_a_weak_voice_match_is_no_match():
    a = People(NAMES).assess("Kitchen", now=0.0, voice=scores(kiril=0.4))
    assert a.speaker is None


def test_a_voice_like_nobody_enrolled_is_unknown():
    a = People(NAMES).assess("Kitchen", now=0.0, voice=scores())
    assert a.speaker is None


def test_voice_and_aimed_face_agreeing():
    p = People(NAMES)
    p.observe("Kitchen", "aimed", "kiril", 0.9, now=10.0)
    a = p.assess("kitchen", now=12.0, voice=scores(kiril=0.7))
    assert a.speaker == "kiril" and a.how == "voice and face" and a.posterior > 0.99


def test_voice_and_aimed_face_disagreeing_names_nobody():
    p = People(NAMES)
    p.observe("Kitchen", "aimed", "maria", 0.9, now=10.0)
    line = p.line("Kitchen", now=12.0, voice=scores(kiril=0.8))
    assert "talking to" not in line
    assert "don't know who is speaking" in line


def test_a_face_the_robot_turned_to_is_the_speaker():
    p = People(NAMES)
    p.observe("Kitchen", "aimed", "maria", 0.9, now=10.0)
    assert "talking to Maria" in p.line("Kitchen", now=11.0)


def test_a_face_merely_in_the_room_is_nearby_not_the_speaker():
    p = People(NAMES)
    p.observe("Kitchen", "face", "maria", 0.9, now=10.0)
    line = p.line("Kitchen", now=11.0)
    assert "talking to" not in line and "Maria is nearby" in line


def test_presence_narrows_a_close_voice_call():
    # Kiril and Ivo sound alike; only Kiril's phone is here: Kiril.
    p = People(NAMES)
    p.observe("Kitchen", "phone", "kiril", 1.0, now=0.0)
    a = p.assess("Kitchen", now=30.0, voice=scores(kiril=0.62, ivo=0.6))
    assert a.speaker == "kiril"
    # Without the phone the same voice is too close to call.
    assert People(NAMES).assess("Kitchen", now=30.0, voice=scores(kiril=0.62, ivo=0.6)).speaker is None


def test_a_phone_left_elsewhere_does_not_beat_a_clear_voice():
    p = People(NAMES)
    p.observe("Kitchen", "phone", "maria", 1.0, now=0.0)
    a = p.assess("Kitchen", now=30.0, voice=scores(kiril=0.75))
    assert a.speaker == "kiril"
    assert "Maria is nearby" in p.line("Kitchen", now=30.0, voice=scores(kiril=0.75))


def test_the_same_speaker_carries_through_a_conversation():
    p = People(NAMES)
    close = scores(kiril=0.6, ivo=0.58)
    assert p.assess("Kitchen", now=0.0, voice=close).speaker is None
    assert p.assess("Kitchen", now=0.0, voice=close, last="kiril").speaker == "kiril"


def test_two_faces_name_nobody():
    p = People(NAMES)
    p.observe("Kitchen", "face", "maria", 0.9, now=10.0)
    p.observe("Kitchen", "face", "ivo", 0.9, now=10.0)
    line = p.line("Kitchen", now=11.0)
    assert "talking to" not in line
    assert "Maria" in line and "Ivo" in line


def test_evidence_expires():
    p = People(NAMES)
    p.observe("Kitchen", "aimed", "kiril", 0.9, now=0.0)
    p.observe("Kitchen", "phone", "maria", 1.0, now=0.0)
    assert "Kiril" not in p.line("Kitchen", now=30.0)   # the face is 30 s old
    assert "Maria" in p.line("Kitchen", now=30.0)       # the phone still counts
    assert p.line("Kitchen", now=200.0) is None         # so is the phone, after 2 minutes


def test_other_rooms_are_other_rooms():
    p = People(NAMES)
    p.observe("Office", "aimed", "kiril", 0.9, now=0.0)
    assert p.line("Kitchen", now=1.0) is None


def test_unknown_people_and_sources_are_dropped():
    p = People(NAMES)
    p.observe("Kitchen", "face", "mallory", 0.99, now=0.0)
    p.observe("Kitchen", "smell", "kiril", 0.99, now=0.0)
    p.observe("Kitchen", "face", "kiril", "very", now=0.0)
    assert p.line("Kitchen", now=1.0) is None
    assert People(NAMES).assess("Kitchen", now=0.0, voice={"mallory": 0.99}).speaker is None


def test_a_face_that_left_is_forgotten():
    p = People(NAMES)
    p.observe("Kitchen", "face", "kiril", 0.9, now=0.0)
    p.forget_faces("Kitchen")
    assert p.line("Kitchen", now=1.0) is None


def test_who_to_learn_from():
    p = People(NAMES)
    # A clear voice alone: learn.
    assert p.assess("Kitchen", now=0.0, voice=scores(kiril=0.85)).learn == "kiril"
    # A face the robot turned to teaches the voice, even when the voice was unsure.
    p.observe("Kitchen", "aimed", "maria", 0.9, now=0.0)
    assert p.assess("Kitchen", now=1.0, voice=scores(maria=0.5)).learn == "maria"
    # A named speaker on a middling voice alone: not sure enough to learn from.
    q = People(NAMES)
    q.observe("Kitchen", "phone", "kiril", 1.0, now=0.0)
    assert q.assess("Kitchen", now=1.0, voice=scores(kiril=0.62, ivo=0.6)).learn is None


def test_being_home_tips_a_close_call_but_never_overrules():
    # Kiril sounds a little more like himself than Ivo does: too close to
    # call alone; Kiril's phone on the home network tips it. Home is weaker
    # than a phone in the room (test_presence_narrows_a_close_voice_call).
    close = scores(kiril=0.68, ivo=0.6)
    assert People(NAMES).assess("Kitchen", now=10.0, voice=close).speaker is None
    p = People(NAMES)
    p.observe_home(["kiril", "ghost"], now=0.0)
    assert p.home(now=10.0) == {"kiril"}  # unknown keys are dropped
    a = p.assess("Kitchen", now=10.0, voice=close)
    assert a.speaker == "kiril"
    # Home is not nearby: it says nothing about this room.
    assert a.nearby == []
    # A clear voice of someone whose phone is away still wins.
    assert p.assess("Kitchen", now=10.0, voice=scores(ivo=0.75)).speaker == "ivo"


def test_home_is_replaced_and_expires():
    p = People(NAMES)
    p.observe_home(["kiril", "maria"], now=0.0)
    p.observe_home(["maria"], now=60.0)
    assert p.home(now=61.0) == {"maria"}
    assert p.home(now=60.0 + 181) == set()  # the house sensor went quiet
    p.observe_home("kiril", now=300.0)  # not a list: ignored
    assert p.home(now=300.0) == set()


def test_a_phone_heard_through_a_wall_counts_where_it_is_loudest():
    p = People(NAMES)
    p.observe_phone("Kitchen", "kiril", -60, now=0.0)
    p.observe_phone("Bedroom 1", "kiril", -80, now=0.0)  # the same phone, through the wall
    assert "Kiril" in p.line("Kitchen", now=10.0)
    assert p.line("Bedroom 1", now=10.0) is None
    # Close enough in both: in both (a doorway, a thin wall).
    p.observe_phone("Bedroom 1", "kiril", -64, now=20.0)
    assert "Kiril" in p.line("Bedroom 1", now=30.0)
    # Gone quiet in the kitchen: the bedroom alone has it.
    assert "Kiril" in p.line("Bedroom 1", now=125.0)
    assert p.line("Kitchen", now=125.0) is None


def test_bluetooth_and_wifi_strengths_are_not_compared():
    p = People(NAMES)
    p.observe_phone("Kitchen", "maria", -85, now=0.0, via="wifi")
    p.observe_phone("Bedroom 1", "maria", -55, now=0.0, via="ble")
    assert "Maria" in p.line("Kitchen", now=1.0)
    assert "Maria" in p.line("Bedroom 1", now=1.0)


def test_strength_must_be_a_number_and_a_person():
    p = People(NAMES)
    p.observe_phone("Kitchen", "kiril", "loud", now=0.0)
    p.observe_phone("Kitchen", "ghost", -50, now=0.0)
    assert p.line("Kitchen", now=1.0) is None
