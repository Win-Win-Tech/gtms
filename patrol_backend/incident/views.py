from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from .models import incidentreport
from .serializers import IncidentSerializer
from twilio.rest import Client
from django.utils import timezone
import os

class IncidentReportView(APIView):
    def post(self, request):
        data = request.data.copy()
        data['created_by'] = request.user.id
        serializer = IncidentSerializer(data=data)
        if serializer.is_valid():
            incident = serializer.save()

            # Twilio client setup
            client = Client(settings.TWILIO_SID, settings.TWILIO_AUTH_TOKEN)
            photo_filename = os.path.basename(incident.photo.name) if incident.photo else "N/A"            #Send WhatsApp message
            whatsapp_body = (
                f"🚨 Incident Alert 🚨\n"
                f"Severity: {incident.severity}\n"
                f"Description: {incident.incident_description}\n"
                f"Ticket: {incident.ticket_number}\n"
                f"Status: {incident.status}\n"
                f"Timestamp: {incident.created_on.strftime('%Y-%m-%d %H:%M:%S')}"
                f"Photo: {photo_filename}"
            )

            # Media URLs (must be HTTPS and publicly accessible)
            media_urls = []
            if incident.photo:
                media_urls.append(incident.photo)  # e.g., 'http://localhost:8000/media/incidents/EAF399B3-396/1.jpg'
            #if incident.video:
            #    media_urls.append(incident.video)  # e.g., 'https://yourdomain.com/path/to/video.mp4'


            # client.messages.create(
            #     body=whatsapp_body,
            #     from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
            #     to='whatsapp:' + settings.ADMIN_WHATSAPP
            # )

            #Trigger phone call

            # client.calls.create(
            #     twiml=f'<Response><Say>Alert! A {incident.severity} incident has been reported. Ticket {incident.ticket_number}.</Say></Response>',
            #     to=settings.ADMIN_PHONE,
            #     from_=settings.TWILIO_PHONE
            # )

            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class IncidentAssignView(APIView):
    def post(self, request, ticket_number):
        try:
            incident = incidentreport.objects.get(ticket_number=ticket_number)
        except incidentreport.DoesNotExist:
            return Response({'error': 'Incident not found'}, status=status.HTTP_404_NOT_FOUND)

        incident.assigned_by = request.user
        incident.assigned_to_id = request.data.get('assigned_to')
        incident.assigned_on = timezone.now()
        incident.save()
        return Response({'message': 'Incident assigned successfully'}, status=status.HTTP_200_OK)

class IncidentResolveView(APIView):
    def post(self, request, ticket_number):
        try:
            incident = incidentreport.objects.get(ticket_number=ticket_number)
        except incidentreport.DoesNotExist:
            return Response({'error': 'Incident not found'}, status=status.HTTP_404_NOT_FOUND)

        incident.resolved_by = request.user
        incident.resolved_on = timezone.now()
        incident.closure_description = request.data.get('closure_description', '')
        incident.save()
        return Response({'message': 'Incident resolved successfully'}, status=status.HTTP_200_OK)


from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.db.models import Q
from .models import incidentreport
from .serializers import IncidentSerializer
import datetime

class IncidentFilterView(APIView):
    def get(self, request):
        # Get query parameters
        date_filter = request.query_params.get('date_filter', '').lower()
        #status_filter = request.query_params.get('status', '').capitalize()
        status_filter = request.query_params.get('status', '').title()
        severity_filter = request.query_params.get('severity', '').capitalize()
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        # Base queryset
        queryset = incidentreport.objects.all()

        # Apply status filter
        if status_filter in ['Open', 'In-Progress', 'Closed']:
            queryset = queryset.filter(status=status_filter)

        if severity_filter in ['Low', 'Medium', 'High']:
            queryset = queryset.filter(severity=severity_filter)

        # Apply date filter
        now = timezone.now()
        if date_filter == 'today':
            queryset = queryset.filter(created_on__date=now.date())
        elif date_filter == 'this_week':
            start_of_week = now - datetime.timedelta(days=now.weekday())
            queryset = queryset.filter(created_on__date__gte=start_of_week.date())
        elif date_filter == 'this_month':
            queryset = queryset.filter(created_on__year=now.year, created_on__month=now.month)
        elif date_filter == 'custom' and start_date and end_date:
            try:
                start = datetime.datetime.strptime(start_date, '%Y-%m-%d')
                end = datetime.datetime.strptime(end_date, '%Y-%m-%d') + datetime.timedelta(days=1)
                queryset = queryset.filter(created_on__range=(start, end))
            except ValueError:
                return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        # Serialize and return
        serializer = IncidentSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

