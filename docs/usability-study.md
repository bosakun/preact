# Future Tree Comprehension Study

Status: protocol prepared; no participant results collected. This tests the accepted
M9 goal: at least four of five unfamiliar viewers distinguish evidence within
30 seconds. It is not an official organizer submission requirement or sponsor validation.

## Prepare the session

Recruit five English-speaking viewers who have not seen PreAct or its explanation.
Obtain consent; use anonymous IDs P1–P5 and do not commit identifying information.
Record the tested release revision, served runtime source hash, URL, browser,
viewport, operation mode, run ID and a screenshot of the initial view. Preserve
the same release and viewport across sessions. Do not substitute screenshots or
scripted browser assertions for real participants using the application.

Complete actual Software and Physical runs before the study. Show the Future Tree,
legend and evidence panels at the latest event of one run; retain its genuine
observed path and alternatives. Alternate initial worlds: Software for P1/P3/P5,
Physical for P2/P4. Keep any replay label visible. Local MuJoCo/model scope remains
local evidence. Document the initial decision round; use the same setup per world.

## Run and score

Do not give a walkthrough or explain the legend beforehand. Start a timer when
the prepared application becomes visible and say:

“Explain which things on this screen are possible futures, which have been checked,
and which actually happened.”

Stop scoring after 30 seconds. Record an anonymous verbatim answer and elapsed
time. A pass requires all three distinctions without prompting:

- Candidate branches describe possibilities; they did not all happen.
- Verified evidence checks a proposed future; verification does not itself mean
  the action was committed to the observed world.
- The selected first action has an observed outcome; unchosen branches have no
  measured outcome labels.

Do not award a pass for repeating badge text without explaining its meaning.
Have a second reviewer apply the same rubric to the recorded answer; resolve and
record disagreements without changing the rubric after seeing results.

After timed scoring, ask the viewer to inspect a rejected branch, locate its gate
reason, find uncertainty/engine disagreement, and find prediction error/trust after
execution. Then show the other world. Record navigation problems and whether
prediction, generated visuals, executable checks and simulation are confused.
These exploratory tasks do not retroactively change the primary score.

## Evidence and acceptance

| Viewer | Initial world | Answer / elapsed | Three distinctions | Reviewer agreement | Result |
| --- | --- | --- | --- | --- | --- |
| P1 | Software | Pending | Pending | Pending | Not tested |
| P2 | Physical | Pending | Pending | Pending | Not tested |
| P3 | Software | Pending | Pending | Pending | Not tested |
| P4 | Physical | Pending | Pending | Pending | Not tested |
| P5 | Software | Pending | Pending | Pending | Not tested |

Archive setup metadata, consent confirmation, anonymous answers, scores and failures
under a dated report. Four of five primary passes satisfy this narrow comprehension
gate; the small, mixed-world convenience sample does not establish broad usability.
Report secondary-world learning effects separately. If the gate fails, fix observed
confusion and repeat with five new viewers, preserving prior results. Recheck after
material changes to the judged interface. Until actual results exist, M9/J2 stays open.
