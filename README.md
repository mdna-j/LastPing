# LastPing

LastPing is a desktop application built in Python that continuously monitors the health of network services and applications. Rather than simply reporting whether a service is online or offline, LastPing collects historical performance metrics to help identify recurring failures, performance degradation, and long-term reliability trends.

The goal of the project is to provide developers and system administrators with a lightweight monitoring tool that not only detects outages but also helps explain why they occur through historical analysis.

---

## Features (Planned)

### Monitoring
- HTTP/HTTPS endpoint monitoring
- TCP port monitoring
- DNS lookup monitoring
- Configurable monitoring intervals
- Automatic retry handling

### Data Collection
- Response time tracking
- Status code recording
- Failure reason logging
- Historical uptime records
- Latency history

### Analytics
- Uptime percentage
- Average response time
- Failure frequency
- Historical trend analysis
- Performance visualization

### Alerts
- Desktop notifications
- Email alerts
- Discord webhook support
- Configurable alert thresholds

### Desktop Application
- Service dashboard
- Real-time monitoring status
- Historical graphs
- Service management
- Dark mode (planned)

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python |
| Desktop GUI | PySide6 (Qt) |
| Database | PostgreSQL |
| ORM | SQLModel |
| Scheduler | APScheduler |
| Networking | Requests, Socket |
| Charts | PyQtGraph / Matplotlib |
| Version Control | Git & GitHub |

---

## Project Goals

- Build a professional desktop application.
- Learn modern software architecture.
- Improve backend and data engineering skills.
- Gain experience collecting, storing, and analyzing operational metrics.
- Produce a portfolio-quality project suitable for software engineering and data engineering interviews.

---

## Roadmap

### Version 0.1
- [ ] Project setup
- [ ] Database schema
- [ ] Desktop window
- [ ] Add/Delete monitored services

### Version 0.2
- [ ] HTTP monitoring
- [ ] Background scheduler
- [ ] Save monitoring history

### Version 0.3
- [ ] Dashboard
- [ ] Historical metrics
- [ ] Charts

### Version 0.4
- [ ] Alerting system
- [ ] Notifications
- [ ] Error logging

### Version 1.0
- [ ] Complete desktop monitoring application
- [ ] Analytics dashboard
- [ ] Export reports
- [ ] Installer

---

## Status

🚧 Currently under active development.

This project is being rebuilt from the ground up with an emphasis on clean architecture, maintainability, and real-world software engineering practices.