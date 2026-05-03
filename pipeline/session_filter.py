from datetime import datetime, timezone

def get_session_info():
    hour = datetime.now(timezone.utc).hour

    if hour in [10, 11]:
        return {'action': 'FULL', 'size': 1.0, 'label': 'London Peak 76-77%', 'trade': True}
    if hour in [5, 6, 7, 8, 9]:
        return {'action': 'FULL', 'size': 1.0, 'label': 'Asia-London 71-74%', 'trade': True}
    if hour in [19, 20, 21]:
        return {'action': 'FULL', 'size': 1.0, 'label': 'NY Evening 71-81%', 'trade': True}
    if hour in [12, 13, 14, 15]:
        return {'action': 'HALF', 'size': 0.5, 'label': 'LDN/NY Overlap 64-70%', 'trade': True}
    if hour in [17, 18, 22]:
        return {'action': 'HALF', 'size': 0.5, 'label': 'NY 65-68%', 'trade': True}
    if hour == 16:
        return {'action': 'SKIP', 'size': 0.0, 'label': 'NY Open 55% SKIP', 'trade': False}
    return {'action': 'HALF', 'size': 0.5, 'label': 'Off hours', 'trade': True}
