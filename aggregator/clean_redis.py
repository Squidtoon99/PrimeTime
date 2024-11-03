from redis import Redis 


client = Redis(decode_responses=True)

for key in client.scan_iter("sessions:*"):
    client.delete(key)
    print("Deleting: ", key)

client.delete("sessions")

print("Done :)")