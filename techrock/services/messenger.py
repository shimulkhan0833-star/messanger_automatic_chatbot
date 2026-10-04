"""Outgoing Messenger API calls."""
import os

# Send a text reply to the Messenger user using the Page access token.
# Raise an error if Meta rejects the request so the worker can handle the failure.
async def send_reply(client, sender, text):
    version = os.getenv('META_GRAPH_VERSION', 'v24.0')
    response = await client.post(f'https://graph.facebook.com/{version}/me/messages',
        headers={'Authorization': 'Bearer ' + os.environ['META_PAGE_ACCESS_TOKEN']},
        json={'recipient': {'id': sender}, 'messaging_type': 'RESPONSE', 'message': {'text': text}})
    response.raise_for_status()


