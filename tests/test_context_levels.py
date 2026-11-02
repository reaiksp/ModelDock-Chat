import copy
import pytest
from chatdock.context_levels import CONVERSATION_CONTEXTS, context_reserve, saved_conversation


def test_five_levels_are_exact_2k_steps():
    assert list(CONVERSATION_CONTEXTS.values()) == [2048,4096,6144,8192,10240]
    assert [context_reserve(x) for x in CONVERSATION_CONTEXTS.values()] == [0,0,.75,1.5,2.125]


@pytest.mark.parametrize('old,new', [(1,2),(2,2),(3,4),(4,5),(5,5)])
def test_old_preferences_migrate_once_without_mutating_original(old,new):
    settings={'conversation_demand':old, 'custom':'keep', 'context':16384}
    before=copy.deepcopy(settings)
    assert saved_conversation(settings)==new and settings==before
    marked=dict(settings,conversation_demand=new,conversation_scale=2)
    assert saved_conversation(marked)==new


def test_fresh_defaults_remain_4k_but_new_lowest_is_2k():
    assert saved_conversation({})==2
    assert saved_conversation({'conversation_demand':1,'conversation_scale':2})==1
    assert saved_conversation({'capacity':'context'})==5
