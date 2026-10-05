import asyncio
import json
import unittest
from unittest.mock import patch
from langchain_core.runnables import RunnableLambda
from techrock.services.chatbot import generate_reply

class HandoffContextTests(unittest.TestCase):
    def generate(self, decisions, text, history):
        seen = []
        outputs = iter(decisions)
        async def respond(prompt):
            seen.append(prompt.to_messages())
            return json.dumps(next(outputs))
        with patch('langchain_groq.ChatGroq', return_value=RunnableLambda(respond)):
            result = asyncio.run(generate_reply(text, history))
        return result, seen

    def test_old_handoff_cannot_pause_greeting_again(self):
        result, seen = self.generate([{'action': 'handoff'}, {'action': 'reply', 'text': 'Hello!'}],
            'hi', [('human', 'I want to speak to a person')])
        self.assertEqual(result, 'Hello!')
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(seen[1]), 2)
        self.assertEqual(seen[1][-1].content, 'hi')

    def test_new_request_still_hands_off(self):
        result, _ = self.generate([{'action': 'handoff'}, {'action': 'handoff'}],
            'Please get someone from your team', [('human', 'Hello')])
        self.assertIsNone(result)

    def test_normal_reply_needs_only_one_call(self):
        result, seen = self.generate([{'action': 'reply', 'text': 'An answer'}],
            'What are earbuds?', [('human', 'Hello')])
        self.assertEqual(result, 'An answer')
        self.assertEqual(len(seen), 1)

    def test_handoff_without_history_needs_one_call(self):
        result, seen = self.generate([{'action': 'handoff'}], 'Human please', [])
        self.assertIsNone(result)
        self.assertEqual(len(seen), 1)
