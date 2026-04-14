from django.db import models
from django.contrib.auth.models import User


class TripItinerary(models.Model):
    user                 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='trips')
    destination          = models.CharField(max_length=200)
    origin               = models.CharField(max_length=200, blank=True, default='')  # NEW: departing from
    days                 = models.PositiveIntegerField()
    budget               = models.FloatField()
    travel_type          = models.CharField(max_length=50)
    members              = models.PositiveIntegerField(default=1)
    result_text          = models.TextField()
    is_budget_sufficient = models.BooleanField(default=True)
    created_at           = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        route = f"{self.origin} → " if self.origin else ""
        return f"{self.user.username} — {route}{self.destination} ({self.days}D, {self.members} pax)"