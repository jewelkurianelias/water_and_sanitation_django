from django.db import models

class KidsCard(models.Model):
    card_order = models.IntegerField(db_index=True, unique=True)
    title = models.CharField(max_length=200)
    lead_html = models.TextField()
    detail_html = models.TextField()
    hint_text = models.CharField(max_length=120, default="Tap me to unlock the fish!")
    read_seconds = models.PositiveSmallIntegerField(default=4)
    is_starter = models.BooleanField(default=False)

    class Meta:
        ordering = ["card_order"]
        db_table = "kids_card" 
        managed = False 


#kid-map




class AnimalSighting(models.Model):
    sighting_id = models.AutoField(primary_key=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    common_name = models.CharField(max_length=255)
    size_text = models.CharField(max_length=64, null=True, blank=True)
    comparison = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        ordering = ["sighting_id"]
        db_table = "sightings" 
        managed = False