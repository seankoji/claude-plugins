# Fixture Project

Marker: GOLDFISH_STUB_NO_VERDICT — tells tests/lib/stubs/gemini to omit any
VERDICT line entirely, exercising goldfish-judge.sh's fail-closed exit code 2
path (classify() returns ERROR when no VERDICT line is found in the report).
