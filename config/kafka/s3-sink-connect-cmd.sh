curl -i -X PUT -H "Accept:application/json" \
    -H "Content-Type:application/json" http://localhost:8083/connectors/sink-s3-voluble/config \
    -d '
 {
	"connector.class": "io.confluent.connect.s3.S3SinkConnector",
	"key.converter": "org.apache.kafka.connect.json.JsonConverter",
	"value.converter": "org.apache.kafka.connect.json.JsonConverter",
	"value.converter.schemas.enable": "false",
	"tasks.max": "1",
	"topics": "audit-event-log",
	"s3.region": "us-east-1",
	"s3.bucket.name": "playground",
	"flush.size": "3",
	"storage.class": "io.confluent.connect.s3.storage.S3Storage",
	"format.class": "io.confluent.connect.s3.format.json.JsonFormat",
	"s3.endpoint": "http://object-storage:9000",
	"store.url": "http://object-storage:9000",
	"confluent.topic.bootstrap.servers": "kafka:29092",
    "aws.access.key.id": "jupyter",
    "aws.secret.access.key": "jupyter@analytics"
}'