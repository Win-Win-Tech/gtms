from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from django.conf import settings
from django.utils import timezone
from django.db.models import Q
from datetime import datetime, timedelta
from .models import incidentreport
from .serializers import IncidentSerializer
from twilio.rest import Client
from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    get_user_today,
    get_user_now,
    convert_date_range_to_utc,
    to_user_timezone
)
import os
import cloudinary
import cloudinary.uploader

# Cloudinary configuration (can also be placed in settings.py)
# cloudinary.config(
#     cloud_name=settings.CLOUDINARY_CLOUD_NAME,
#     api_key=settings.CLOUDINARY_API_KEY,
#     api_secret=settings.CLOUDINARY_API_SECRET
# )

class IncidentReportView(APIView):
        parser_classes = [MultiPartParser]

        def post(self, request):
            data = request.data.copy()
            data['created_by'] = request.user.id
            data['location'] = request.user.location_id
            serializer = IncidentSerializer(data=data)

            if serializer.is_valid():
                incident = serializer.save()

                # Upload media to Cloudinary
                media_urls = []
                photo_url = None
                video_url = None

                if incident.photo:
                    upload_result = cloudinary.uploader.upload(incident.photo.file)
                    photo_url = upload_result.get('secure_url')
                    media_urls.append(photo_url)

                if incident.video:
                    upload_result = cloudinary.uploader.upload(
                        incident.video.file,
                        resource_type="video"
                    )
                    video_url = upload_result.get('secure_url')
                    media_urls.append(video_url)

                # Twilio client setup
                client = Client(settings.TWILIO_SID, settings.TWILIO_AUTH_TOKEN)

                # Convert timestamp to user timezone for display
                user_tz = get_user_timezone_from_request(request)
                created_on_user = to_user_timezone(incident.created_on, user_tz)

                whatsapp_body = (
                    f"🚨 Incident Alert 🚨\n"
                    f"Severity: {incident.severity}\n"
                    f"Description: {incident.incident_description}\n"
                    f"Ticket: {incident.ticket_number}\n"
                    f"Status: {incident.status}\n"
                    f"Timestamp: {created_on_user.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Photo: {os.path.basename(incident.photo.name) if incident.photo else 'N/A'}"
                )

                # Send WhatsApp message with photo or text
                if photo_url:
                    client.messages.create(
                        body=whatsapp_body,
                        from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
                        to='whatsapp:' + settings.ADMIN_WHATSAPP,
                        media_url=[photo_url]
                    )
                else:
                    client.messages.create(
                        body=whatsapp_body,
                        from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
                        to='whatsapp:' + settings.ADMIN_WHATSAPP
                    )

                # Send video separately if present
                if video_url:
                    client.messages.create(
                        body=f"🎥 Incident Video for Ticket {incident.ticket_number}",
                        from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
                        to='whatsapp:' + settings.ADMIN_WHATSAPP,
                        media_url=[video_url]
                    )

                # Trigger phone call
                client.calls.create(
                    twiml=f'<Response><Say>Alert! A {incident.severity} incident has been reported. Ticket {incident.ticket_number}.</Say></Response>',
                    to=settings.ADMIN_PHONE,
                    from_=settings.TWILIO_PHONE
                )

                return Response(serializer.data, status=status.HTTP_201_CREATED)

            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# class IncidentReportView(APIView):
#     parser_classes = [MultiPartParser]

#     def post(self, request):
#         data = request.data.copy()
#         data['created_by'] = request.user.id
#         data['location'] = request.user.location_id
#         serializer = IncidentSerializer(data=data)

#         if serializer.is_valid():
#             incident = serializer.save()

#             # Upload media to Cloudinary
#             media_urls = []

#             if incident.photo:
# #               upload_result = cloudinary.uploader.upload(incident.photo.path)
#                 upload_result = cloudinary.uploader.upload(incident.photo.file)
#                 photo_url = upload_result.get('secure_url')
#                 media_urls.append(photo_url)
#             else:
#                 photo_url = "N/A"

#             if incident.video:
#                 upload_result = cloudinary.uploader.upload(incident.video.path, resource_type="video")
#                 video_url = upload_result.get('secure_url')
#                 media_urls.append(video_url)

#             # Twilio client setup
#             client = Client(settings.TWILIO_SID, settings.TWILIO_AUTH_TOKEN)

#             whatsapp_body = (
#                 f"🚨 Incident Alert 🚨\n"
#                 f"Severity: {incident.severity}\n"
#                 f"Description: {incident.incident_description}\n"
#                 f"Ticket: {incident.ticket_number}\n"
#                 f"Status: {incident.status}\n"
#                 f"Timestamp: {incident.created_on.strftime('%Y-%m-%d %H:%M:%S')}\n"
#                 f"Photo: {os.path.basename(incident.photo.name) if incident.photo else 'N/A'}"
#             )

#             # Send WhatsApp message
#             message = client.messages.create(
#                 body=whatsapp_body,
#                 from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
#                 to='whatsapp:' + settings.ADMIN_WHATSAPP,
#                 media_url=media_urls if media_urls else None
#             )

#             #         #Trigger phone call

#             client.calls.create(
#                  twiml=f'<Response><Say>Alert! A {incident.severity} incident has been reported. Ticket {incident.ticket_number}.</Say></Response>',
#                  to=settings.ADMIN_PHONE,
#                  from_=settings.TWILIO_PHONE
#             )

#             # Log message details
#             print("Message SID:", message.sid)
#             print("Status:", message.status)
#             print("To:", message.to)
#             print("From:", message.from_)
#             print("Date Created:", message.date_created)
#             print("Media:", message.media)

#             return Response(serializer.data, status=status.HTTP_201_CREATED)

#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    #/////////////29-Oct/////////////
    # def post(self, request):
    #     data = request.data.copy()
    #     data['created_by'] = request.user.id
    #     data['location'] = request.user.location_id
    #     serializer = IncidentSerializer(data=data)
    #     if serializer.is_valid():
    #         incident = serializer.save()

    #         # Twilio client setup
    #         client = Client(settings.TWILIO_SID, settings.TWILIO_AUTH_TOKEN)
    #         photo_filename = os.path.basename(incident.photo.name) if incident.photo else "N/A"            #Send WhatsApp message
    #         whatsapp_body = (
    #             f"🚨 Incident Alert 🚨\n"
    #             f"Severity: {incident.severity}\n"
    #             f"Description: {incident.incident_description}\n"
    #             f"Ticket: {incident.ticket_number}\n"
    #             f"Status: {incident.status}\n"
    #             f"Timestamp: {incident.created_on.strftime('%Y-%m-%d %H:%M:%S')}"
    #             f"Photo: {photo_filename}"
    #         )

    #         # Media URLs (must be HTTPS and publicly accessible)
    #         media_urls = []
    #         if incident.photo:
    #            media_urls=[incident.photo]  # e.g., 'http://localhost:8000/media/incidents/EAF399B3-396/1.jpg'
    #         else:
    #            media_urls = None 
    #         if incident.video:
    #            media_urls.append(incident.video)  # e.g., 'https://yourdomain.com/path/to/video.mp4'

    #         #media_url = incident.photo.url if incident.photo else None
    #         #media_url = [request.build_absolute_uri(incident.photo.url)] if incident.photo else None

    #         #print ("media_urls:", media_urls)

    #         message = client.messages.create(
    #              body=whatsapp_body,
    #              from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
    #              to='whatsapp:' + settings.ADMIN_WHATSAPP,
    #              media_url=media_urls                
    #         )
    #         # # Print response details
    #         print("Message SID:", message.sid)
    #         print("Status:", message.status)
    #         print("To:", message.to)
    #         print("From:", message.from_)
    #         print("Date Created:", message.date_created)
    #         print("Media:", message.media)


    #         #Trigger phone call

    #         # client.calls.create(
    #         #      twiml=f'<Response><Say>Alert! A {incident.severity} incident has been reported. Ticket {incident.ticket_number}.</Say></Response>',
    #         #      to=settings.ADMIN_PHONE,
    #         #      from_=settings.TWILIO_PHONE
    #         # )

    #         return Response(serializer.data, status=status.HTTP_201_CREATED)
    #     return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    #/////////////29-Oct/////////////


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


# Duplicate imports removed - using imports from top of file

# class IncidentFilterView(APIView):
#     def get(self, request):
#         # Get query parameters
#         date_filter = request.query_params.get('date_filter', '').lower()
#         #status_filter = request.query_params.get('status', '').capitalize()
#         status_filter = request.query_params.get('status', '').title()
#         severity_filter = request.query_params.get('severity', '').capitalize()
#         start_date = request.query_params.get('start_date')
#         end_date = request.query_params.get('end_date')

#                 # Base queryset
#         queryset = incidentreport.objects.all()

#         # Apply status filter
#         if status_filter in ['Open', 'In-Progress', 'Closed']:
#             queryset = queryset.filter(status=status_filter)

#         if severity_filter in ['Low', 'Medium', 'High']:
#             queryset = queryset.filter(severity=severity_filter)

#         # Apply date filter
#         now = timezone.now()
#         if date_filter == 'today':
#             queryset = queryset.filter(created_on__date=now.date())
#         elif date_filter == 'this_week':
#             start_of_week = now - datetime.timedelta(days=now.weekday())
#             queryset = queryset.filter(created_on__date__gte=start_of_week.date())
#         elif date_filter == 'this_month':
#             queryset = queryset.filter(created_on__year=now.year, created_on__month=now.month)
#         elif date_filter == 'custom' and start_date and end_date:
#             try:
#                 start = datetime.datetime.strptime(start_date, '%Y-%m-%d')
#                 end = datetime.datetime.strptime(end_date, '%Y-%m-%d') + datetime.timedelta(days=1)
#                 queryset = queryset.filter(created_on__range=(start, end))
#             except ValueError:
#                 return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

#         # Serialize and return
#         serializer = IncidentSerializer(queryset, many=True)
#         return Response(serializer.data, status=status.HTTP_200_OK)


class IncidentFilterView(APIView):
    def get(self, request):
        # Get query parameters
        date_filter = request.query_params.get('date_filter', '').lower()
        status_filter = request.query_params.get('status', '').title()
        severity_filter = request.query_params.get('severity', '').capitalize()
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        # New filters
        location_id = request.query_params.get('location_id')
        assigned_to_id = request.query_params.get('assigned_to')
        created_by_id = request.query_params.get('created_by')
        checkpoint_id = request.query_params.get('checkpoint_id')

        # Base queryset
        queryset = incidentreport.objects.all()

        # Apply status filter
        if status_filter in ['Open', 'In-Progress', 'Closed']:
            queryset = queryset.filter(status=status_filter)

        # Apply severity filter
        if severity_filter in ['Low', 'Medium', 'High']:
            queryset = queryset.filter(severity=severity_filter)

        # Apply date filter - use user timezone
        user_tz = get_user_timezone_from_request(request)
        user_now = get_user_now(user_tz)
        user_today = user_now.date()
        
        if date_filter == 'today':
            # Convert today to UTC date range for query
            start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'this_week':
            start_of_week = user_today - timedelta(days=user_today.weekday())
            end_of_week = start_of_week + timedelta(days=6)
            start_utc, end_utc = convert_date_range_to_utc(start_of_week, end_of_week, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'this_month':
            # Get month boundaries in user timezone
            start_of_month = user_today.replace(day=1)
            if user_today.month == 12:
                end_of_month = user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end_of_month = user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1)
            start_utc, end_utc = convert_date_range_to_utc(start_of_month, end_of_month, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'custom' and start_date and end_date:
            try:
                # Parse dates as user timezone dates
                start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
                end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
                # Convert to UTC range for query
                start_utc, end_utc = convert_date_range_to_utc(start_date_obj, end_date_obj, user_tz)
                queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
            except ValueError:
                return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        # Apply new filters
        if location_id:
            queryset = queryset.filter(location_id=location_id)
        if assigned_to_id:
            queryset = queryset.filter(assigned_to_id=assigned_to_id)
        if created_by_id:
            queryset = queryset.filter(created_by_id=created_by_id)
        if checkpoint_id:
            queryset = queryset.filter(checkpoint_id=checkpoint_id)

        # Serialize and return
        serializer = IncidentSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

