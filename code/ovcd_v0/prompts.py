"""Scope card (口径卡 v0, fixed) and the T3 verify / T5 box prompts."""

CLASS = 'building'

SCOPE_CARD = {
    'counts': ['a building newly constructed (absent in T1, present in T2)',
               'a building demolished (present in T1, absent in T2)',
               'a building demolished and rebuilt (a different building in T2)'],
    'not_counts': ['shadows',
                   'seasonal, illumination or color/tone differences of the same building',
                   'edge responses caused by misregistration between T1 and T2',
                   'vehicles and temporary piles or materials',
                   'changes of non-building land cover (roads, vegetation, bare soil)'],
}

PSEUDO_TYPES = ['new_building', 'demolished', 'rebuilt', 'shadow', 'seasonal_or_color',
                'misregistration', 'vehicle_or_temporary', 'non_building_change', 'no_change']

SYSTEM = (
    'You verify changes of the class "building" between two co-registered remote sensing images '
    'of the same place: T1 (earlier) and T2 (later).\n'
    'Scope card for "building".\n'
    'COUNTS as a building change:\n' + ''.join(f'- {x}\n' for x in SCOPE_CARD['counts']) +
    'Does NOT count:\n' + ''.join(f'- {x}\n' for x in SCOPE_CARD['not_counts']) +
    'Quote the scope card when you decide.')

FRAMING = ('T1 close-up (earlier):', 'T2 close-up (later):')

VERIFY = (
    'The red contour marks the same region in both close-ups. Is there a building change inside the '
    'red contour according to the scope card? Reply with JSON only: '
    '{"change": "yes" or "no", "type": one of ' + str(PSEUDO_TYPES).replace("'", '"') +
    ', "reason": one short sentence}')
PREFIX = '{"change": "'

BOX = (
    'The red contour marks a suspicious region in both close-ups. Draw one tight bounding box around '
    'the building that changed between T1 and T2 at this location (the building present in T2 if it '
    'is new, in T1 if it was demolished). Use coordinates of the close-up normalized to 0-1000. '
    'Reply with JSON only: {"bbox_2d": [x1, y1, x2, y2]}. If there is no changed building, reply '
    '{"bbox_2d": []}.')
BOX_PREFIX = '{"bbox_2d": ['


def messages(t1, t2, question):
    content = []
    for label, im in zip(FRAMING, (t1, t2)):
        content += [{'type': 'text', 'text': label}, {'type': 'image', 'image': im}]
    content.append({'type': 'text', 'text': question})
    return [{'role': 'system', 'content': [{'type': 'text', 'text': SYSTEM}]},
            {'role': 'user', 'content': content}]
