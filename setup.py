from setuptools import Extension, setup
from Cython.Build import cythonize


extensions = [
    Extension("api.customer_api", ["api/customer_api.py"]),
    Extension("api.smartflo_client", ["api/smartflo_client.py"]),
    Extension("db.database", ["db/database.py"]),
    Extension("db.models.customer", ["db/models/customer.py"]),
    Extension("db.models.call_metadata", ["db/models/call_metadata.py"]),
    Extension("db.models.conversation", ["db/models/conversation.py"]),
    Extension("db.models.customer_feedback", ["db/models/customer_feedback.py"]),
    Extension("db.utils", ["db/utils.py"]),
    Extension("agent.metrics", ["agent/metrics.py"]),
    Extension("agent.db_storage", ["agent/db_storage.py"]),
    Extension("agent.survey_agent", ["agent/survey_agent.py"]),
    Extension("agent.web_rtc_server", ["agent/web_rtc_server.py"]),
    Extension("smartflow_bridge", ["smart-flo/smartflow_bridge.py"]),
    Extension("scripts.backfill_feedback_with_llm", ["scripts/backfill_feedback_with_llm.py"]),
]


setup(
    name="livekit_flow_compiled",
    ext_modules=cythonize(
        extensions,
        compiler_directives={"language_level": "3"},
    ),
)
