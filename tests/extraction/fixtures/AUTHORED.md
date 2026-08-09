# These fixtures are **authored**, not captured

Every JSON file in this directory was written by hand from the *documented* response schema
(`docs/AWS-CONSTRAINTS.md`, verified 2026-08-09). **No call has been made to any AWS service
from this repository, so no response has ever been captured.**

That distinction is the whole reason this file exists. A fixture presented as a recorded
response is evidence about a service; a fixture authored from documentation is evidence about
the *adapter* and about the documentation being read correctly. The second is a smaller claim
and it is the true one, and `docs/DECISIONS.md` 16 requires it to be said in the fixture, in the
test name and in the README.

What these therefore prove: that the mapping handles the documented shape, including the parts
this system does not use, and that it refuses a response that does not match rather than
quietly producing a short reading.

What they cannot prove: that the service returns this shape. Only a call could show that, and
no call is made.
