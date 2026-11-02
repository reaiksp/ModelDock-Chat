"""Five recommendation levels in 2K increments; legacy active plans stay intact."""
import math

CONVERSATION_CONTEXTS = {level: level * 2048 for level in range(1, 6)}
LEGACY_CONTEXTS = {1: 4096, 2: 4096, 3: 8192, 4: 16384, 5: 32768}
CONVERSATION_SCALE = 2


def context_reserve(context):
    # Existing runtime estimates use a 4K baseline. Do not claim an unmeasured
    # saving below it. Interpolate conservatively between existing reserve anchors.
    if context <= 4096:
        return 0.0
    if context <= 8192:
        return (context - 4096) / 4096 * 1.5
    if context <= 16384:
        return 1.5 + (context - 8192) / 8192 * 2.5
    return 4.0 + (context - 16384) / 16384 * 4.0


def demand_for_context(context):
    return max(1, min(5, math.ceil(context / 2048)))


def saved_conversation(settings):
    value = settings.get('conversation_demand')
    if type(value) is not int or not 1 <= value <= 5:
        value = 4 if settings.get('capacity') == 'context' else 2
    if settings.get('conversation_scale') == CONVERSATION_SCALE:
        return value
    return demand_for_context(LEGACY_CONTEXTS[value])
