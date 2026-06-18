import logging

# The suite deliberately drives many error/failure paths (bad codecs, GPU
# fallback, unserializable settings, etc.). Those paths log at WARNING/ERROR,
# which otherwise spams the test console even though the tests pass. Silence
# application logging for the duration of the test run.
logging.disable(logging.CRITICAL)
