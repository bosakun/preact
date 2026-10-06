# Hackathon Requirements

Official materials checked October 6, 2026. [Rules](https://nebiusglobalaihackathon.devpost.com/rules)
prevail over summaries and announcements. This is an engineering extraction, not an
attestation of entrant eligibility or a claim of completed submission.

October 4 implementation recheck: Rules, Resources, Updates and Dates were reopened;
the recorded technology, submission and schedule constraints still apply. Coding's wording
remains Token Factory; the accepted design retains real Sandbox acceptance as its stricter
gate. Resources still conflict with Rules on city-prize attendance. No authenticated form
or entrant eligibility was inferred from these public pages. The later boundary audit reopened
these pages plus both linked judging/winning-project announcements and found no applicable
change; the Rules modules-in-action exception still overrides hardware-only guidance.

October 5 recheck reopened Overview, Rules, Resources, Updates, Dates and both
linked judging/winning-project announcements. The applicable requirements and dates
remain unchanged. The public page banner announces Devpost maintenance on October 7
at 06:00 UTC (15:00 JST); allow for temporary submission-page unavailability. This
is an operational notice, not an extension of the October 30 deadline.

October 6 recheck reopened Overview, Rules, Resources, Updates, Dates and the linked
judging, winning-project and kickoff announcements. The Updates index still lists
seven announcements; applicable requirements and dates are unchanged. Rules continue
to prevail over the Resources city-attendance conflict and older hardware-only
Physical AI guidance. No authenticated submission form or entrant eligibility was
verified. The maintenance notice does not extend the deadline.

## 1. Hard submission requirements

- **H1:** Register on Devpost; finish every required submission field during the window.
- **H2:** Supply a working, consistently installable application matching its description and demo.
- **H3:** Make a real runtime call to Token Factory inference or run on Nebius AI Cloud compute.
- **H4:** Use at least one NVIDIA open-source model. Isaac simulation alone is not a model.
  October 4 Rules/Resources recheck retains this requirement. Actual local Nemotron model
  use can supply model evidence, but cannot replace H3's Nebius runtime requirement or
  T2's requirement for Nemotron through Token Factory.
- **H5:** Select a qualifying track. PreAct targets Coding; Best Apps is the accepted alternative
  if Sandbox access fails, while retaining both domains and real Nemotron inference.
- **H6:** Provide a public GitHub, GitLab or Bitbucket repository with complete code, assets,
  instructions and a detectable open-source license visible in repository About.
- **H7:** Provide a README explaining setup, operation and actual NVIDIA/Nebius usage.
- **H8:** Supply a working demo/hosted app/test-build URL; Physical AI has a URL exception.
- **H9:** Supply a public YouTube video with real functioning footage and permitted media.
  Rules say less than three minutes; target 170 seconds rather than exactly three minutes.
- **H10:** Supply feature/functionality text and feedback on every Nebius/NVIDIA technology used.
- **H11:** Use English or provide translations for description, video, testing and other materials.
- **H12:** Preserve free, unrestricted judge access through judging, including credentials for
  private access. Uncommon proprietary hardware may require physical access on request.

These obligations are in [Rules §§4–5](https://nebiusglobalaihackathon.devpost.com/rules)
and the [submission overview](https://nebiusglobalaihackathon.devpost.com/).

## 2. Track-specific requirements

- **T1 Coding:** Coding/developer agents write, run and test code in Token Factory. Implement
  real ConTree Sandbox verification and execution; verify final wording before category freeze.
- **T2 Best Apps:** Useful app/agent powered by Nemotron on Token Factory. Serverless Jobs
  and Endpoints are encouraged here, explicitly optional.
- **T3 Personal AI:** Always-on private assistant, persistent memory, reusable skills and user
  tools/control using NVIDIA models. This is outside PreAct's chosen scope.
- **T4 Physical AI:** Embodied/edge intelligence coordinated by an agent runtime. Use Serverless
  Jobs for simulation/data/policy evaluation and Endpoints when real-time inference is needed.
  Video includes at least one minute of hardware operating, or functioning key modules if no hardware.
- **T5 Bonuses:** Tavily prize requires runtime Tavily integration. City prizes require attendance
  under Rules; valuable-feedback prize is optional. PreAct does not add decorative Tavily calls.

[Track requirements and prize conditions](https://nebiusglobalaihackathon.devpost.com/rules).
Each track winner receives a Jetson Orin Nano; overall prizes are $20k/$10k/$6k,
Tavily $3k, twenty city prizes $500, ten feedback prizes $100 plus swag.
An entry can receive one overall or track prize and one bonus, subject to Rules.
[Prize listing](https://nebiusglobalaihackathon.devpost.com/).

## 3. Judging criteria

**J0:** Initial pass/fail viability, required technology and theme fit. **J1–J4:** technological
implementation, design, potential impact and quality of idea, each equally weighted, scored
1–5. Tie-breaks follow the criterion order in Rules. Judges may assess only text/images/video;
make the central concept and actual integration understandable without testing.
[Judging announcement](https://nebiusglobalaihackathon.devpost.com/updates/46204-here-s-how-judging-works)
and [Rules §6](https://nebiusglobalaihackathon.devpost.com/rules).

## 4. Recommended but non-mandatory guidance

**G1:** State the problem, intended audience and practical use. **G2:** Name actual models/services
in description, Built With and narration. **G3:** Explain material tool roles and measured impact.
**G4:** Collect onboarding experience, strengths, weaknesses, examples and likelihood of reuse.
Builder credits, Academy, workshops and office hours are available guidance, not mandatory purchases.
[Winning-project guidance](https://nebiusglobalaihackathon.devpost.com/updates/46205-how-to-build-a-winning-project),
[kickoff tips](https://nebiusglobalaihackathon.devpost.com/updates/46203-kickoff-tips),
[Resources](https://nebiusglobalaihackathon.devpost.com/resources).

## 5. Required deliverables

**D1:** Running application/test URL and testing instructions. **D2:** Public licensed repository
with necessary assets. **D3:** Setup/integration README. **D4:** English project description and track.
**D5:** Public YouTube demonstration. **D6:** Technology feedback. **D7:** Significant-update explanation
if the project predates August 26; entrant/team representative and applicable form attestations.
[Submission requirements](https://nebiusglobalaihackathon.devpost.com/rules).

## 6. Technology/integration expectations

**I1:** Runtime usage must be real, not a logo or dormant adapter. An actual Token Factory call
qualifies; a separate Nebius deployment is not universally required.
[Organizer clarification](https://nebiusglobalaihackathon.devpost.com/forum_topics/45163-does-calling-an-nvidia-model-via-token-factory-satisfy-the-must-run-on-nebius-requirement).
**I2:** Record exact NVIDIA model/version, requests and purpose. **I3:** Comply with SDK/model/data
licenses and permissions. **I4:** Meet selected track's technology obligations; optional technology
must not be misrepresented as mandatory. **I5:** Gather feedback from real use, distinguish untested
features, and show integrations in README/demo. [Rules](https://nebiusglobalaihackathon.devpost.com/rules).

## 7. Deadline and submission constraints

- **S1:** Aug 26, 2026 09:00 PDT → Oct 30, 2026 10:00 PDT (17:00 UTC; Oct 31 02:00 JST).
  Internal acceptance target: Oct 29 17:00 UTC.
- **S2:** Judging Dec 1 09:00 PST → Dec 15 12:00 PST; access must persist through Dec 15
  20:00 UTC / Dec 16 05:00 JST. Winners around Jan 11, 2027 noon PST.
- **S3:** Complete required fields and preserve submitted functionality; post-deadline changes
  require applicable organizer permission, except permitted administrative corrections.
- **S4:** Existing work must be significantly updated after the start with an explanation.
  Multiple entries must be substantially different; submit PreAct as one system, not duplicated worlds.
- **S5:** Original work/ownership, third-party rights, no prohibited sponsor preferential support;
  entrant eligibility and representative authorization must be checked before release.

[Schedule](https://nebiusglobalaihackathon.devpost.com/details/dates),
[Rules §§1,3–5,11](https://nebiusglobalaihackathon.devpost.com/rules).
Eligibility includes age of majority, lawful location and absence of organizer/judge/employer/affiliate,
family/household or other prohibited conflicts. Rules specifically exclude Brazil, Quebec, Russia,
Crimea, Cuba, Iran, North Korea and other comprehensively sanctioned/prohibited jurisdictions.
Solo/team entries are permitted; organizer replies say no team-size maximum.
[Team clarification](https://nebiusglobalaihackathon.devpost.com/forum_topics/45344-team-clarification),
[Team size](https://nebiusglobalaihackathon.devpost.com/forum_topics/45324-how-many-members-can-a-team-have-at-max).

## 8. Ambiguities requiring later verification

- **A1:** Resources' city eligibility wording conflicts with Rules; require attendance for that bonus.
- **A2:** Older Physical hardware announcements conflict with current no-hardware alternative; use Rules.
- **A3:** Cached Coding wording mentions Sandboxes while opened Rules say Token Factory. Recheck
  before freeze; real ConTree fulfills the stricter interpretation where access is available.
- **A4:** Credits expiring before judging and beta/GPU availability are unconfirmed. The organizer
  redirects credit questions to Discord; participant suggestions are not commitments.
  [Credits discussion](https://nebiusglobalaihackathon.devpost.com/forum_topics/45352-hackathon-credits-vs-the-30-day-trial-how-do-we-keep-apps-live-for-judging-dec-1-15).
- **A5:** Authenticated submission form fields remain uninspected; check when entrant access exists.
- **A6:** Deployment and Physical discussion pages `/forum_topics/45423-nebius-deployment-clarification`
  and `/forum_topics/45407-physical-ai-projects` failed to load; no inferred answer. Simulation-only
  threads had no authoritative answer. Current Rules explicitly provide operating-module footage.
- **A7:** Exact model/checkpoint availability, licenses, service quota and costs require real checks.
  Organizer confirms Alpamayo can qualify for Physical; this does not replace PreAct's chosen engines.
  [Model clarification](https://nebiusglobalaihackathon.devpost.com/forum_topics/45378-physical-ai-track-can-we-use-alpamayo-instead-of-the-four-listed-models).

All seven [official updates](https://nebiusglobalaihackathon.devpost.com/updates) were inspected,
including the build recording and four session announcements. Their recording/setup advice does
not supersede Rules. Recheck official materials, form and outstanding ambiguities at M12.
See [submission-checklist.md](submission-checklist.md) for the requirement-to-evidence mapping.
