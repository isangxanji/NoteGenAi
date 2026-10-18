
from ai_notes import generate_study_notes

sample_title = "Introduction to Computer Networks"

sample_text = """
A computer network is a group of interconnected computers
and devices that communicate and share resources.

A LAN, or Local Area Network, connects devices within a
limited area, such as a home, school, or office.

A WAN, or Wide Area Network, connects devices over large
geographical areas. The internet is an example of a WAN.

A router forwards data packets between different networks.
A switch connects devices within a local network.
"""

try:
    notes = generate_study_notes(sample_title, sample_text)

    print("\nGenerated study notes:\n")
    print(notes)

except Exception as error:
    print("Note generation failed.")
    print(error)
