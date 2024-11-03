import json
from redis import Redis 
import time 
from datetime import datetime, timedelta 


class Aggregator(object):
    def __init__(self):
        self.client = Redis(decode_responses=True)
    
    def today(self, timestamp: str):
        if not timestamp:
            return False
        return datetime.fromtimestamp(timestamp).date() == datetime.now().date()
    
    def process_event(self, event):
        if not event.get("id"):
            print("Invalid event: ", event)
            return
        self.client.sadd("sessions", event['id'])
        
        
        
        existing = self.client.json().get(f"sessions:{event['id']}") or {}
        
        if not existing:
            recent_id = self.client.get("agg:last_id")
            last_session = self.client.json().get(f"sessions:{recent_id}") or {}
            
            if last_session.get('classification') == event['classification']:
                existing = last_session
        
        _id = (existing or {}).get('id') or event['id']
        if event['state']:
            # Open a new session 
            new_doc = {
                "start": existing.get("start") or event['timestamp'],
                "end": None,
                "duration": 0,
                "classification": event['classification'],
                "apps": existing.get("apps") or [],
                "id": _id
            }
            self.client.json().set(f"sessions:{_id}", "$", new_doc)
            
            self.client.json().arrappend(f"sessions:{_id}", "$.apps", {
                "name": event['app_name'],
                "title": event['win_title'],
                "start": event['timestamp'],
                "end": None,
                "duration": 0,
                "screenshot": event['screenshot'],
                "icon": event['icon'],
            })
        else:
            
            # safety check
            if not existing:
                return
            # Close the session 
            self.client.json().set(f"sessions:{_id}", "$.end", event['timestamp'])
            # close all the apps 
            for app in existing['apps']:
                if not app['end']:
                    app['end'] = event['timestamp']
                    app['duration'] = int(app['end'] - app['start'])
                    self.client.json().arrappend(f"sessions:{_id}", "$.apps", app)
        
        
        existing = self.client.json().get(f"sessions:{_id}")
        print("event: ", event, "existing: ", existing)
        # update the duration of the session
        self.client.json().set(f"sessions:{_id}", "$.duration", int(event['timestamp'] - existing['start']))
        print(f"Updating duration of session {_id} to {event['timestamp'] - existing['start']} from {existing['duration']}")
        # update the duration of the apps
        apps = self.client.json().get(f"sessions:{_id}", "$.apps")
        for pos, app in enumerate(apps[0] if apps else []):
            duration = max(0, (app['end'] or event['timestamp']) - app['start'])
            print(f"Updating duration of {app['name']} to {duration} from {app['duration']}")
            self.client.json().set(f"sessions:{_id}", f"$.apps[{pos}].duration", int(duration))
            
    def run(self):
        n = 0
        while True:
            last_id = self.client.get("agg:last_id") or 0
            
            itm = self.client.xread(streams={"activity": last_id})
            
            if itm and (d := itm[0]):
                print("itm: ", d)
                _id = last_id
                for (_id, event) in d[1]:
                    self.process_event(
                        json.loads(event['data'])
                    )
                    self.client.set("agg:last_id", _id)
            
                # Calculate totals per classification for today
                sessions = self.client.smembers("sessions")
                # clear the totals
                for key in self.client.scan_iter("agg:total:*"):
                    self.client.delete(key)
                
                # Calculate totals per app
                for key in self.client.scan_iter("agg:app:*"):
                    self.client.delete(key)
                    
                for session in sessions:
                    session = self.client.json().get(f"sessions:{session}")
                    if self.today(session['end']):
                        self.client.incr(f"agg:total:{session['classification']}", int(session['duration']))

                        for app in session['apps']:
                            self.client.incr(f"agg:app:{app['name']}", int(app['duration']))
                    
                
                print("New totals: ")
                for key in self.client.scan_iter("agg:total:*"):
                    print(f"{key}: {self.client.get(key)}")
                    
                print("New app totals: ")
                for key in self.client.scan_iter("agg:app:*"):
                    print(f"{key}: {self.client.get(key)}")
                time.sleep(5)
            time.sleep(15)
if __name__ == "__main__":
    agg = Aggregator()
    
    agg.run()