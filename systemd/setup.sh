#!/bin/bash
# Install systemd service and timer for night batch analysis
set -e

echo "Installing night-batch systemd service..."

sudo cp /home/radamon/radacleaner/systemd/night-batch.service /etc/systemd/system/
sudo cp /home/radamon/radacleaner/systemd/night-batch.timer /etc/systemd/system/
sudo cp /home/radamon/radacleaner/systemd/night-batch-stop.timer /etc/systemd/system/
sudo cp /home/radamon/radacleaner/systemd/stop-night-batch.sh /usr/local/bin/stop-night-batch
sudo chmod +x /usr/local/bin/stop-night-batch

sudo systemctl daemon-reload
sudo systemctl enable night-batch.timer
sudo systemctl start night-batch.timer

echo "Done! Timer enabled. Will run daily at 21:00."
echo "To test now: sudo systemctl start night-batch.service"
echo "To stop: stop-night-batch"
