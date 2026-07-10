import os
import pytest

curdir = os.path.dirname(os.path.realpath(__file__))
from saichallenger.common.sai_npu import SaiNpu
from saichallenger.common.sai_phy import SaiPhy
from saichallenger.common.sai_testbed import SaiTestbed
from saichallenger.common.sai_data import SaiObjType

_previous_test_failed = False

_last_failed_module = None
_previous_test_module = None
_current_test_module = None
_module_failed = {}

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    '''
    This code defines a hook, which is executed after each phase
    of a test execution and is responsible for creating a test report.

    The "when" attribute of the test report represents the phase of the test:
      - "setup": the report is generated during the setup phase of the test.
      - "call": the report is generated during the actual execution of the test.
      - "teardown": the report is generated during the teardown phase of the test.

    The outcome of a test can have the following possible values:
      - "passed": the test has passed successfully.
      - "failed": the test has failed.
      - "skipped": the test was skipped intentionally.
      - "error": an unexpected error occurred during the test execution.
      - "xfailed": the test was expected to fail, and it actually failed as expected.
      - "xpassed": the test was expected to fail, but it passed unexpectedly.
    '''

    outcome = yield
    rep = outcome.get_result()

    global _previous_test_failed
    if rep.when == "setup":
        # Store initial outcome of the test
        _previous_test_failed = rep.outcome not in ["passed", "skipped"]
    elif not _previous_test_failed:
        # Update the outcome only in case all previous phases were successful
        _previous_test_failed = rep.outcome not in ["passed", "skipped"]

    global _last_failed_module

    if rep.when == "call" and rep.failed:
        module_name = item.module.__name__
        _last_failed_module = module_name
        _module_failed[module_name] = True


@pytest.fixture
def prev_test_failed():
    global _previous_test_failed
    return _previous_test_failed


@pytest.fixture(scope="module", autouse=True)
def track_module(request):
    config = request.config
    previous_test_module = getattr(config, "_current_test_module", None)
    current_test_module = request.module.__name__

    config._previous_test_module = previous_test_module
    config._current_test_module = current_test_module

    if previous_test_module != getattr(config, "_last_failed_module", None):
        config._last_failed_module = None


@pytest.fixture(scope="module")
def prev_module_failed(track_module):
    config = track_module.config
    last_failed_module = getattr(config, "_last_failed_module", None)
    current_test_module = getattr(config, "_current_test_module", None)
    return last_failed_module is not None and last_failed_module != current_test_module


@pytest.fixture(scope="module")
def has_module_failed(request):
    def _check():
        return getattr(request.config, "_module_failed", {}).get(request.module.__name__, False)
    yield _check


def pytest_addoption(parser):
    parser.addoption("--traffic", action="store_true", help="run tests with traffic")
    parser.addoption("--testbed", action="store", help="Testbed name", required=True)


def pytest_sessionstart(session):
    SaiObjType.generate_from_thrift()
    SaiObjType.generate_from_json()


def pytest_sessionfinish(session, exitstatus):
    """
    Final safety net: if topology cleanup failed and no next test can recover,
    force one last hard reset before session exits.
    """
    if not getattr(session, "_topology_cleanup_failed", False):
        return

    npu = getattr(session, "_session_npu", None)
    if npu is None:
        return

    try:
        npu.reset()
    except Exception:
        pass

    time.sleep(2)

    _cli = getattr(npu, "sai_client", None)
    if _cli and hasattr(_cli, "r"):
        try:
            _cli.r.delete("ASIC_STATE_KEY_VALUE_OP_QUEUE")
            _cli.r.delete("GETRESPONSE_KEY_VALUE_OP_QUEUE")
        except Exception:
            pass


@pytest.fixture(scope="session")
def exec_params(request):
    config_param = {
        # Generic parameters
        "traffic": request.config.getoption("--traffic"),
        "testbed": request.config.getoption("--testbed"),
    }
    return config_param


@pytest.fixture(scope="session")
def testbed_instance(exec_params):
    testbed = SaiTestbed(f"{curdir}/..", exec_params["testbed"], exec_params["traffic"])
    testbed.init()
    yield testbed
    testbed.deinit()


@pytest.fixture(scope="function")
def testbed(testbed_instance):
    testbed_instance.setup()
    yield testbed_instance
    testbed_instance.teardown()


@pytest.fixture(scope="session")
def npu(request, testbed_instance):
    if len(testbed_instance.npu) == 1:
        npu_obj = testbed_instance.npu[0]
        request.session._session_npu = npu_obj
        return npu_obj
    return None


@pytest.fixture(scope="session")
def dpu(testbed_instance):
    if len(testbed_instance.dpu) == 1:
        return testbed_instance.dpu[0]
    return None


@pytest.fixture(scope="session")
def phy(testbed_instance):
    if len(testbed_instance.phy) == 1:
        return testbed_instance.phy[0]
    return None


@pytest.fixture(scope="session")
def dataplane_instance(testbed_instance):
    if len(testbed_instance.dataplane) == 1:
        yield testbed_instance.dataplane[0]
    else:
        yield None


@pytest.fixture(scope="function")
def dataplane(dataplane_instance):
    if dataplane_instance:
        dataplane_instance.setup()
        yield dataplane_instance
        dataplane_instance.teardown()
    else:
        yield None
