echo "Registering..."
amazon-ssm-agent -register -code "${CODE}" -id "${ID}" -region "${REGION}"
echo "Starting agent..."
amazon-ssm-agent start
