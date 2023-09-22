from django.shortcuts import render, redirect

import datetime
import random
import string
import pyotp
from django.core.mail import send_mail
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import JWTAuthentication, create_access_token, create_refresh_token, decode_access_token, decode_refresh_token

from .models import Reset, User, UserToken
from .serializers import UserSerializer

from google.oauth2 import id_token
from google.auth.transport.requests import Request as GoogleRequest


class RegisterAPIView(APIView):
    # @csrf_exempt
    def post(self, request):
        data = request.data
        email = data['email']
        
        user = User.objects.filter(email=email).first()

        if user:
            return Response(data='Email already exists', status=403)
            # raise exceptions.APIException('Email already exists')

        if data['password'] != data['password_confirm']:
            return Response(data='Passwords do not match!', status=401)
            # raise exceptions.APIException('Passwords do not match!')

        serializer = UserSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class LoginAPIView(APIView):
    # @csrf_exempt
    def post(self, request):
        email = request.data['email']
        password = request.data['password']

        user = User.objects.filter(email=email).first()

        if user is None:
            return Response(data='Invalid credentials', status=404)

        if not user.check_password(password):
            return Response(data='Invalid credentials', status=403)
            # content = {'please move along': 'nothing to see here'}
            # return Response(content, status=status.HTTP_404_NOT_FOUND)

        # Raising errors
        # if user is None:
        #     raise exceptions.AuthenticationFailed('Invalid credentials')

        # if not user.check_password(password):
        #     raise exceptions.AuthenticationFailed('Invalid credentials')

        # access_token = create_access_token(user.id)
        # refresh_token = create_refresh_token(user.id)

        # UserToken.objects.create(
        #     user_id=user.id,
        #     token=refresh_token,
        #     expired_at=datetime.datetime.utcnow() + datetime.timedelta(days=7)
        # )

        # response = Response()

        # # refresh token via cookies
        # # httponly is only accessible by the backend (not by frontend)
        # response.set_cookie(key='refresh_token',
        #                     value=refresh_token, httponly=True)

        # # access token via body
        # response.data = {
        #     'token': access_token
        # }
        # # return Response(serializer.data) # User data
        # return response

        if user.tfa_secret:
            return Response({
                'id': user.id
            })

        secret = pyotp.random_base32()
        # otpauth_url = pyotp.totp.TOTP(
        #     secret).provisioning_uri(issuer_name='LeGiDo')
        otpauth_url = pyotp.totp.TOTP(
            secret).provisioning_uri(name=user.email, issuer_name='LeGiDo',)

        return Response({
            'id': user.id,
            'secret': secret,
            'otpauth_url': otpauth_url
        })


class TwoFactorAPIView(APIView):
    def post(self, request):
        id = request.data['id']

        user = User.objects.filter(pk=id).first()

        if not user:
            raise exceptions.AuthenticationFailed('Invalid credentials')

        secret = user.tfa_secret if user.tfa_secret != '' else request.data['secret']

        if not pyotp.TOTP(secret).verify(request.data['code']):
            raise exceptions.AuthenticationFailed('Invalid credentials')

        if user.tfa_secret == '':
            user.tfa_secret = secret
            user.save()

        access_token = create_access_token(id)
        refresh_token = create_refresh_token(id)

        UserToken.objects.create(
            user_id=id,
            token=refresh_token,
            expired_at=datetime.datetime.utcnow() + datetime.timedelta(days=7)
        )

        response = Response()
        # refresh token via cookies
        # httponly is only accessible by the backend (not by frontend)
        response.set_cookie(key='refresh_token',
                            value=refresh_token, httponly=True)

        # access token via body
        response.data = {
            'token': access_token
        }
        # return Response(serializer.data) # User data
        return response


class UserAPIView(APIView):
    authentication_classes = [JWTAuthentication]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class RefreshAPIView(APIView):
    def post(self, request):
        refresh_token = request.COOKIES.get('refresh_token')
        id = decode_refresh_token(refresh_token)

        if not UserToken.objects.filter(
            user_id=id,
            token=refresh_token,
            expired_at__gt=datetime.datetime.now(tz=datetime.timezone.utc)
        ).exists():
            raise exceptions.AuthenticationFailed('unauthenticated')

        access_token = create_access_token(id)

        return Response({
            'token': access_token
        })


class LogoutAPIView(APIView):

    def post(self, request):
        refresh_token = request.COOKIES.get('refresh_token')
        UserToken.objects.filter(token=refresh_token).delete()

        response = Response()
        response.delete_cookie(key='refresh_token')
        response.data = {
            'message': 'success'
        }
        return response


class ForgotAPIView(APIView):
    def post(self, request):
        email = request.data['email']
        token = ''.join(random.choice(string.ascii_lowercase +
                        string.digits) for _ in range(10))

        Reset.objects.create(
            email=email,
            token=token
        )

        url = 'http://localhost:3000/reset/' + token

        send_mail(
            subject='Reset your password!',
            message='Click <a href="%s">here</> to reset your password!' % url,
            from_email="from@example.com",
            recipient_list=[email]
        )

        return Response({
            'message': 'success'
        })


class ResetAPIView(APIView):
    def post(self, request):

        data = request.data
        if data['password'] != data['password_confirm']:
            raise exceptions.APIException('Passwords do not match!')

        reset_password = Reset.objects.filter(token=data['token']).first()

        if not reset_password:
            raise exceptions.APIException('Invalid link!')

        user = User.objects.filter(email=reset_password.email).first()

        if not user:
            raise exceptions.APIException('User not found!')

        user.set_password(data['password'])
        user.save()

        return Response({
            'message': 'success'
        })


class GoogleAuthAPIView(APIView):
    def post(self, request):
        token = request.data['token']

        googleUser = id_token.verify_token(token, GoogleRequest())

        if not googleUser:
            raise exceptions.AuthenticationFailed('unauthenticated')

        user = User.objects.filter(email=googleUser['email']).first()

        if not user:
            user = User.objects.create(
                first_name=googleUser['given_name'],
                last_name=googleUser['family_name'],
                email=googleUser['email']
            )
            user.set_password(token)
            user.save()

        print('googleUser: ', googleUser)
        print('user: ', user)
        #
        # Refactoring möglich???
        #
        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)

        UserToken.objects.create(
            user_id=user.id,
            token=refresh_token,
            expired_at=datetime.datetime.utcnow() + datetime.timedelta(days=7)
        )

        response = Response()
        # refresh token via cookies
        # httponly is only accessible by the backend (not by frontend)
        response.set_cookie(key='refresh_token',
                            value=refresh_token, httponly=True)

        # access token via body
        response.data = {
            'token': access_token
        }
        # return Response(serializer.data) # User data
        return response
