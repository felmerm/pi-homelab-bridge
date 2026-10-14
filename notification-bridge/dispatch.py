"""Typed Pi actions. Future Alexa input adapters call the same validated API."""
from actions import Action


async def execute_action(action, alexa, hue):
    if action.kind == 'lights':
        await hue.set_lights(action.target, action.color)
    elif action.kind == 'sequence':
        # Local and Amazon operations cannot share an Amazon-only serial node.
        # Batch adjacent speech/media; finish local lighting before submitting
        # the following batch. Never send lights as Alexa text commands.
        pending = []
        for step in action.steps:
            if step.kind == 'lights':
                if pending:
                    await alexa.execute(Action('sequence', steps=tuple(pending)))
                    pending.clear()
                await hue.set_lights(step.target, step.color)
            else:
                pending.append(step)
        if pending:
            await alexa.execute(Action('sequence', steps=tuple(pending)))
    else:
        await alexa.execute(action)


def needs_alexa(action):
    if action.kind == 'sequence':
        return any(needs_alexa(step) for step in action.steps)
    return action.kind in ('announce', 'speak', 'audio')
