import pytest
from assistant_router.gate import gate


@pytest.mark.parametrize("text", ["Turn.", "Play.", "Turn off.", "Turn on, bye.", "Done.", "please",
                                  "Okay. Okay. Okay.", "Thank you.", "Blame.", "What?", "", "   "])
def test_fragments_and_fillers_rejected(text):
    assert gate(text, "") == "reject"


def test_bleed_rejected():
    long = ("except the evil out of them. Well, the old term for personality disorders, "
            "and this goes into the way we think about people today.")
    assert gate(long, "") == "reject"


def test_echo_of_last_reply_rejected():
    assert gate("Okay, it's on done.", "Okay, it's on. Done.") == "reject"
    assert gate("I can't play and stop the radios. Let me know if you need anything else.",
                "I can't play and stop the radio. Let me know if you need anything else.") == "reject"


@pytest.mark.parametrize("text", ["Stop.", "Stop! Stop!", "Please stop.", "Cancel.", "Never mind."])
def test_stop_words(text):
    assert gate(text, "") == "stop"


@pytest.mark.parametrize("text", ["Tell me something about Lisbon Portugal.", "Can you read the news for me please?",
                                  "Why is the heating on?", "Remind me to call mum", "Set the Remind for 730",
                                  "Play the Dark Net Diary podcast.", "Play La Isla Bonita by Madonna",
                                  "Play something relaxing", "turn off all lights", "Lights off everywhere"])
def test_fast_path_to_cloud(text):
    assert gate(text, "") == "escalate"


@pytest.mark.parametrize("text", ["Turn off the office light.", "volume up", "Pause the video", "Turn on bedroom or sunlight."])
def test_device_commands_pass_to_triage(text):
    assert gate(text, "") == "pass"


def test_a_repeated_command_is_not_an_echo():
    assert gate("Turn off the office light.", "Turned off the office light.") == "pass"


def test_radio_requests_go_to_the_cloud():
    # Deliberate: "the radio" is a Music Assistant matter (search, stations), not a local device.
    assert gate("Turn off the radio.", "") == "escalate"


def test_bare_pause_is_a_command():
    assert gate("Pause.", "") == "pass"


@pytest.mark.parametrize("text", ["to melt the bathroom light.", "that we can even hold out of it.",
                                  "Whenever a neutrino interacts with one of the billions of ideals, that interaction is big.",
                                  "and then the lights went out"])
def test_continuations_of_someone_elses_sentence_rejected(text):
    assert gate(text, "") == "reject"


@pytest.mark.parametrize("text", ["Okay, it's on done.", "I can't play that. Let me know if you need anything else."])
def test_assistant_phrasing_rejected_without_memory(text):
    # Another satellite's reply has no entry in this satellite's memory.
    assert gate(text, "") == "reject"


def test_two_sentences_of_speech_rejected():
    assert gate("Stop the music, stay back, please. So, maybe you can't be able to eat some of that.", "") == "reject"


def test_short_two_sentence_command_passes():
    assert gate("Louder. Okay, louder.", "") == "pass"


@pytest.mark.parametrize("text", ["Turn off the lights", "Turn off the lights in the house",
                                  "Turn off the TV in the bedroom and the kitchen", "Close the blinds"])
def test_several_devices_go_to_the_cloud(text):
    assert gate(text, "") == "escalate"


def test_an_echo_of_sorry_is_rejected():
    assert gate("Sorry, I didn't catch that.", "Sorry, I didn't catch that.") == "reject"


@pytest.mark.parametrize("text", ["Yes", "No", "yes please", "no thanks", "Sure", "Yeah", "Nope", "okay"])
def test_a_short_answer_to_a_question_goes_on(text):
    asked = "Do you want me to turn on the kitchen light too?"
    assert gate(text, asked) == "escalate"
    assert gate(text, "Turned on the kitchen light.") == "reject"  # no question: still a fragment
    assert gate(text, "") == "reject"
