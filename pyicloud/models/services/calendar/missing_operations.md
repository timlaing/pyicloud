# HTTP Operations not yet implemented in calendar.py service

================================================================================

1. update_calendar

================================================================================

PURPOSE: Updates an existing calendar
METHOD: POST
URL: {service_root}/ca/collections/{calendar.guid}
PATH PARAMS: - #TODO: determine path params for update_calendar
QUERY PARAMS: - #TODO: determine query params for update_calendar
PAYLOAD: #TODO: determine payload format for update_calendar
RESPONSE: #TODO: determine response format for update_calendar

================================================================================

update_event

================================================================================

PURPOSE: Updates an existing event
METHOD: POST
URL: {service_root}/ca/events/{event.pguid}/{event.guid}
PATH PARAMS: - #TODO: determine path params for update_event
QUERY PARAMS: - #TODO: determine query params for update_event
PAYLOAD: #TODO: determine payload format for update_event
RESPONSE: #TODO: determine response format for update_event

================================================================================

Idle

================================================================================

PURPOSE: Unknown purpose
METHOD: POST
URL: {service_root}/ca/idle
PATH PARAMS: - #TODO: determine path params for idle
QUERY PARAMS: - #TODO: determine query params for idle
PAYLOAD: #TODO: determine payload format for idle
RESPONSE: #TODO: determine response format for idle

================================================================================

alarmtriggers

================================================================================

PURPOSE: Unknown purpose
METHOD: GET
URL: {service_root}/alarmtriggers
PATH PARAMS: - #TODO: determine path params for alarmtriggers
QUERY PARAMS: - #TODO: determine query params for alarmtriggers
PAYLOAD: #TODO: determine payload format for alarmtriggers
RESPONSE: #TODO: determine response format for alarmtriggers

================================================================================

State

================================================================================

PURPOSE: Unknown purpose
METHOD: GET
URL: {service_root}/ca/state
PATH PARAMS: - #TODO: determine path params for State
QUERY PARAMS: - #TODO: determine query params for State
PAYLOAD: #TODO: determine payload format for State
RESPONSE: #TODO: determine response format for State

================================================================================

serverpreferences

================================================================================

PURPOSE: Unknown purpose
METHOD: POST
URL: {service_root}/ca/serverpreferences
PATH PARAMS: - #TODO: determine path params for serverpreferences
QUERY PARAMS: - #TODO: determine query params for serverpreferences
PAYLOAD: #TODO: determine payload format for serverpreferences
RESPONSE: #TODO: determine response format for serverpreferences

================================================================================

Remove all events from a recurring event

================================================================================

PURPOSE: Removes all events from a recurring event
METHOD: POST
URL: {service_root}/ca/events/{event.pguid}/{event.guid}\_\_20250802T100000Z/all
PATH PARAMS: - #TODO: determine path params for remove all events from a recurring event
QUERY PARAMS: - #TODO: determine query params for remove all events from a recurring event
PAYLOAD: #TODO: determine payload format for remove all events from a recurring event
RESPONSE: #TODO: determine response format for remove all events from a recurring event

================================================================================

attachment

================================================================================

PURPOSE: attach a file to an event
METHOD: POST
URL: {service_root}/ca/attachment/{event.pguid}/{event.guid}
PATH PARAMS: - #TODO: determine path params for attachment
QUERY PARAMS: - #TODO: determine query params for attachment - example:
[
{'X-name': 'folo_logo.png'},
{'X-type': 'image%2Fpng'},
{'ctag': 'HwoQEgwAAQPbkyhd0AAAAAAYAxgAIhUIzZ7FhsqY69QyEPbZ8rWG86q0pAEoAEgA'},
{'lang': 'en-US'},
{'usertz': 'Europe%2FParis'},
{'requestID': '132'},
{'ifMatch': 'mdp8vzll'},
{'startDate': '2025-07-26'},
{'endDate': '2025-09-06'},
{'clientBuildNumber': '2526Project38'},
{'clientMasteringNumber': '2526B20'},
{'clientId': '93cf465f-eb5a-4f4a-8043-f7bcbd9b57ac'},
{'dsid': '10927495723'}
]
PAYLOAD: #TODO: determine payload format for attachment
RESPONSE: #TODO: determine response format for attachment
