--Conversational scrubbing--

Package 2: audio transcription an text PII scrubbing.

-Overview-
this component turns spoken audio into text and removes personally identifiable information from that text before it reaches storage.  It takes an auio or video file, produces a transcript, and passes transcription through PII detaction an redaction.  It covers step 3 and 4 of the processing flow in architecture.md, sitting between the visual scrubber and the token manager.

Two modules are involved. robopii/audio_processor.py handles transcription. robopii/text_scrubber.py handles detection and redaction.

--Transcription--

-Model choice-
Whisiper is used from speech to text.  It runs locally and needs no API key, which matches the breifs preference for local or edge execution over cloud dependence.  It also means no audio is sent to a third party, which would be difficult to defen in a privacy tool.

The base model is used by deafault.  It was chosen as a starting point for speed on laptop without a GPU.  tiny and small have not been compared against it yet, so this choice should be revisited during performance evaluation rather than treated as settled.

-Setup-
ffmpeg is required and is not a python package, so pip install -r requirments.txt does not provide it.  ON macOS:

brew install ffmpeg

Whisper downloads its model on first use, about 140 MB for base.

On macOS this download can fail with an SSL certificate error.  Running Install Certificates.command from the python folder in Applications fixes it.  The model is cached after the first successful download.

-How it works-
1. The input file is checked to exist
2. ffmpeg extracts the audio track into a temporary 16 kHz mono wav file.  The video stream is never read (-vn)
3. Whisper transcribes the temporary file
4. The temporary file is deleted
5. A TranscriptResult is returned with the transcript and processing time.

The temporary file is deleted because it is a complete unredacted recording of whoever was speaking.  Leaving it on disk woul efet the purpose of the tool in the same way that storing unblurred frames would.  It is written to the system temporary directory rather than anywhere in the project, so its never at risk of being committed.

--Findings form testing--
Test phrases were sythesised with macOS say command and transcribed with the base model.  No recordings of real people were used, so there are no consent or storage concerns with the test data and the expected output is known exactly.

-Email addresses do not survive transcription-

Spoken: My email is jane dot smith at example dot com
Transcribed: jane.smithadexample.com

Spoken: My email is jane.smith@example.com
Transcribed: James smith at example.com

Neither output containes an @ symbol, so a standard email pattern matches nothing in either case.  Two attempts prouced two different failiure modes.  In the first, "dot", became a real full stop but "at" was obsorbed into the surrounding words as "ad".  In the second, the local part was reinterperated as a persons name an the @ was spelled out as a seperate word.

This is not a bug to be fixed, speech does not contain punctuation, so there is no reliable spoken form of an email address for a pattern to match.  Normalising the transcript afterwars does not solve it wither, since that would require guessing that "ad" was meant to be @.

-Phone numbers are regrouped and can lose digits

-Test 1-
Spoken: 0412 345 678
Transcribed: 041-2345-678

-Test 2-
Spoken: oh four one two, three four five, six seven eight
Transcribed: 041-2-345-678

-Test 3-
Spoken: Zero four one two three four five six seven eight
Transcribed: 041-345678

The same ten digit mobile number was written three different ways, with hyphens rather than spaces and with groups broken in different places.  The third result contains only nine digits, so a digit was lost entirley.

For scrubbing this is survivable.  A pattern that matches ten digits with arbitrary seperators still finds the number and redacts it, so no PII escapes. It does mean a pattern written for the Australian format 04XX XXX XXX will miss all three of these.

For anything that treats a phone number as an identifier is not survivable.  A number that loses a digit cannot be matched against the same number transcribed correctly on another occasion.

-Names rift between transcriptions-

Spoken: AIden Channell
Transcribed: Aiden Channel

Spoken: Jane Smith
Transcribed: James Smith

Named entity recognition still tags both of these as people, so detection and redaction work as intended.

The problem is consistency.  The same person might be transcribed differently every time thy speak, and in the second case forst name changed entirely.  ANy attempt to link a returning person in their transcript will therefore be unreadable.  This matters for package 3, where tokens are meant to be reused for recurring entities.

-Addresses and data survives well enough-

Spoken: 42 Northbourne Avenue, Canberra
Transcribed: 42 Northborne Avenue, Canberra

Spoken: the third of June 2004
Transcribed:the 3rd of June 2004

Only minor drift here.  The street name lost a letter an the date name was normalised form words to an ordinal, but the number, street type, suburb an full date all survived intact and still detectable.