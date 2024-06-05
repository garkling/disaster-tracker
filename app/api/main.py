import logging
import os
from typing import Annotated, List
from unittest import case

from fastapi import FastAPI, Cookie, Response, Header, Depends, HTTPException
from fastapi.requests import Request
from googleapiclient.errors import HttpError

from app.db import EventStorage
from app.calendar_events import CalendarTracker, CalendarApi, CalendarEvent, Calendar
from app.disaster_events import DisasterTracker, NasaEonetEventAPI, DisasterAlert
from app.notifier import AlertNotifier
from app.pipeline import AsyncPipeline, Something, Ok, Err, Nothing
from app.session import ClientSession
from app.preferences import UserPreferences, Preferences
from app.watcher import EventChannels, WatchedCalendars, ChannelInfo, WatchBody
from app.credentials import ServiceCredentialApi, OAuthCredentials, ServiceCredentialStorage


os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'


app = FastAPI()
logger = logging.getLogger(__name__)

channels = EventChannels()
watched_calendars = WatchedCalendars()
user_sessions = ClientSession()
user_preferences = UserPreferences()
credential_storage = ServiceCredentialStorage()


async def get_session_email(user_session_id: Annotated[str | None, Cookie()] = None) -> str:
    match await user_sessions.get(user_session_id):
        case Ok(Something(user_email)): return user_email
        case Ok(_): raise HTTPException(status_code=401, detail="Not authenticated")

        case err: raise HTTPException(status_code=500, detail=f"Internal error: {err.err()}")


async def get_credentials(user_email: Annotated[str, Depends(get_session_email)]):

    async def _get_or_refresh_credentials(credentials: OAuthCredentials):
        api = ServiceCredentialApi()
        if not api.credentials_valid(credentials):
            match await api.refresh(credentials):
                case Ok(fresh): return fresh
                case _: raise HTTPException(status_code=401, detail="Unable to refresh the credentials")   # todo: add logic to notify a user

        return credentials

    match await credential_storage.get(user_email):
        case Ok(Something(credentials)): return await _get_or_refresh_credentials(credentials)
        case Ok(_): raise HTTPException(status_code=401, detail="Not authenticated")

        case err: raise HTTPException(status_code=500, detail=f"Internal error: {err.err()}")


@app.get("/auth")
async def auth():
    match await ServiceCredentialApi().request():
        case Ok([auth_url, _]):
            print(auth_url)
            return dict(link=auth_url)

        case Err(err): raise HTTPException(status_code=500, detail=str(err))


@app.get("/oauth2callback", status_code=200)
async def oauth2callback(
        state: str,
        request: Request,
        response: Response,
):

    credential_api = ServiceCredentialApi()
    match await credential_api.obtain(state, request_uri=str(request.url)):
        case Ok(credentials):
            match await credential_api.fetch_user_info(credentials):
                case Ok(user_info):
                    email = user_info["email"]

                case err: raise HTTPException(status_code=401, detail=str(err.err()))
        case err: raise HTTPException(status_code=401, detail=str(err.err()))

    match await credential_storage.save(email, credentials):
        case Ok(True):
            match await user_sessions.create(email):
                case Ok(user_session_id): pass
                case err: raise err.err()

        case Ok(False): raise HTTPException(status_code=500)
        case err: raise HTTPException(status_code=401, detail=str(err.err()))

    print(user_session_id)
    response.set_cookie(key='user_session_id', value=user_session_id, httponly=True)
    return dict(message="Successfully logged in via Google OAuth2", status_code=200)


@app.get('/logout', status_code=200)
async def logout(
        user_session_id: Annotated[str, Cookie()],
        user_email: Annotated[str, Depends(get_session_email)],
        credentials: Annotated[OAuthCredentials, Depends(get_credentials)]
):
    match (
        await (await (await ServiceCredentialApi().revoke(credentials))
               .then_async(
                lambda _: credential_storage.delete(user_email)))
            .then_async(
                lambda _: user_sessions.delete(user_session_id)
        )
    ):
        case Ok(_): return dict(message="Successfully logged out", status_code=200)
        case _: raise HTTPException(status_code=500, detail="Something went wrong")


@app.get('/preferences', response_model=Preferences)
async def get_preferences(user_email: Annotated[str, Depends(get_session_email)]):
    match await user_preferences.get(user_email):
        case Ok(prefs): return prefs
        case err: raise HTTPException(status_code=500, detail=f"Internal error: {err.err()}")


@app.post('/preferences')
async def set_preferences(prefs: Preferences, user_email: Annotated[str, Depends(get_session_email)]):
    match await user_preferences.create(user_email, prefs):
        case Ok(_): return dict(status_code=201, message="Preferences created successfully")
        case err: raise HTTPException(status_code=500, detail=f"Internal error: {err.err()}")


@app.post("/watch")
async def watch(
        body: WatchBody,
        user_email: Annotated[str, Depends(get_session_email)],
        credentials: Annotated[OAuthCredentials, Depends(get_credentials)],
):
    api = CalendarApi(credentials)
    notifier = AlertNotifier(credentials)
    ct = CalendarTracker(EventStorage("calendar_events"))
    dt = DisasterTracker(EventStorage("disaster_events"))

    match api.list_events(body.calendar_id):
        case Ok(event_pipe):
            for event in event_pipe:
                match await dt.check_calendar_event(event):
                    case Something(alert):
                        match notifier.notify(alert):
                            case Ok(_):
                                logger.info(f"{alert}: Email notification alert sent successfully")
                                # match await ct.save_as_tracked(event):
                                #     case Err(err):
                                #         logger.error(f"{event}: failed to update the event status - {err}")

                            case Err(err): logger.error(f"{alert}: Email notification delivery failed - {err}")

                match await ct.create_or_update_event(event):
                    case Err(err): logger.error(f"{event}: failed to save the event - {err}")

        case Err(HttpError() as e): raise HTTPException(status_code=e.status_code, detail=e.error_details)
        case Err(err): raise HTTPException(status_code=500, detail=f"Something went wrong - {err}")

    match api.watch_calendar(body.calendar_id):
        case Ok(channel_id):
            match (await watched_calendars.create(body.calendar_id, channel_id)).then_async(
                lambda _: channels.create(ChannelInfo(id=channel_id, user_email=user_email, calendar_id=body.calendar_id))
            ):
                case Ok(_): return dict(message=f"Subscribed to {body.calendar_id} events", status_code=201)
                case Err(err): raise HTTPException(status_code=500, detail=f"Something went wrong - {err}")

        case Err(HttpError() as e): raise HTTPException(status_code=e.status_code, detail=e.error_details)
        case Err(err): raise HTTPException(status_code=500, detail=f"Something went wrong - {err}")


@app.post("/unwatch")
async def unwatch(
        body: WatchBody,
        credentials: Annotated[OAuthCredentials, Depends(get_credentials)]
):
    match await watched_calendars.get(body.calendar_id):
        case Ok(Something(channel_id)):
            api = CalendarApi(credentials)
            match api.unwatch_calendar(body.calendar_id, channel_id):
                case Ok(_):
                    await watched_calendars.delete(body.calendar_id)
                    await channels.delete(channel_id)
                    return dict(message=f"Unsubscribed to {body.calendar_id} events", status_code=204)
                case _: raise HTTPException(status_code=500, detail="Something went wrong")

        case Ok(Nothing()): raise HTTPException(status_code=404, detail=f"Watchers for {body.calendar_id} are not exist")


@app.post("/listen_calendar_events", status_code=201)
async def listen_calendar_events(
        x_goog_channel_id: Annotated[str , Header()],
        x_goog_resource_id: Annotated[str, Header()],
        x_goog_resource_state: Annotated[str, Header()],
):

    match await channels.get(x_goog_channel_id):
        case Ok(Something(channel_info)):
            credentials = await get_credentials(channel_info.user_email)

        case Ok(Nothing()): raise HTTPException(status_code=404)
        case _: raise HTTPException(status_code=500)

    ct = CalendarTracker(EventStorage("calendar_events"))

    match x_goog_resource_state:
        case "not_exists":
            match await ct.delete_event(x_goog_resource_id):
                case Err(_): raise HTTPException(status_code=500)

        case "exists" | "sync":
            dt = DisasterTracker(EventStorage("disaster_events"))
            match CalendarApi(credentials).get_event(
                calendar_id=channel_info.calendar_id,
                event_id=x_goog_resource_id
            ):
                case Ok(event):
                    notifier = AlertNotifier(credentials)
                    match await (
                            await ct.sync_event(event)

                    ).then_async(dt.check_calendar_event):
                        case Ok(Something(alert)):
                            match notifier.notify(alert):
                                case Ok(_):
                                    pass
                                    # await ct.save_as_tracked(event)

                                case _: raise HTTPException(status_code=500)

                            return dict(status_code=204)

                        case Ok(_): return dict(status_code=204)
                        case _: raise HTTPException(status_code=500)


@app.post("/listen_calendar_events_mock", status_code=201)
async def listen_calendar_events(
        body: WatchBody,
        x_goog_resource_id: Annotated[str, Header()],
        x_goog_resource_state: Annotated[str, Header()],
        credentials: Annotated[OAuthCredentials, Depends(get_credentials)]
):
    ct = CalendarTracker(EventStorage("calendar_events"))

    match x_goog_resource_state:
        case "not_exists":
            match await ct.delete_event(x_goog_resource_id):
                case Err(_): raise HTTPException(status_code=500)

        case "exists" | "sync":
            dt = DisasterTracker(EventStorage("disaster_events"))
            match CalendarApi(credentials).get_event(
                calendar_id=body.calendar_id,
                event_id=x_goog_resource_id
            ):
                case Ok(Something(event)):
                    notifier = AlertNotifier(credentials)
                    match await (
                            await ct.sync_event(event)

                    ).then_async(dt.check_calendar_event):
                        case Something(alert):
                            match notifier.notify(alert):
                                case Ok(_):
                                    pass
                                    # await ct.save_as_tracked(event)

                                case _: raise HTTPException(status_code=500)

                            return dict(status_code=204)

                        case _: return dict(status_code=204)

                case Ok(_): return dict(status_code=204)
                case Err(err):
                    raise HTTPException(status_code=500, detail=f"Something went wrong - {err}")


@app.get("/calendars/{calendar_id}/events")
async def get_events(calendar_id: str, credentials: Annotated[OAuthCredentials, Depends(get_credentials)]) -> List[CalendarEvent]:
    api = CalendarApi(credentials)

    match api.list_events(calendar_id):
        case Ok(events): return events.collect_as(list).something() or []
        case Err(HttpError() as e): raise HTTPException(status_code=e.status_code, detail=str(e))
        case _: raise HTTPException(status_code=500, detail="Something went wrong")


@app.get("/calendars/{calendar_id}/event/{event_id}")
async def get_event(calendar_id: str, event_id, credentials: Annotated[OAuthCredentials, Depends(get_credentials)]) -> CalendarEvent:
    api = CalendarApi(credentials)

    match api.get_event(calendar_id, event_id):
        case Ok(Something(event)): return event
        case Err(HttpError() as e): raise HTTPException(status_code=e.status_code, detail=str(e))
        case _: raise HTTPException(status_code=500, detail="Something went wrong")


@app.get("/calendars")
async def get_calendars(credentials: Annotated[OAuthCredentials, Depends(get_credentials)]) -> List[Calendar]:
    api = CalendarApi(credentials)

    match api.list_calendars():
        case Something(events):
            return events.collect_as(list).something() or []

    return []


@app.post("/scheduled_check")
async def scheduled_check():
    ct = CalendarTracker(EventStorage("calendar_events"))
    dt = DisasterTracker(EventStorage("disaster_events"))

    async def _find_and_notify(alert: DisasterAlert) -> str:
        credentials = await get_credentials(alert.recipient)
        match AlertNotifier(credentials).notify(alert):
            case Ok(sent): return sent
            case Err(err): return str(err)

    match await ct.list_events_scheduled_in(days=14):
        case Ok(event_pipe):
            event_pipe: AsyncPipeline
            res = await (
                event_pipe
                .map(dt.check_calendar_event)
                .filter_map(lambda e: e.something())
                .map(_find_and_notify)
                .collect_as(list)
            )
            return res.something() or []

        case Err(err): raise HTTPException(status_code=500, detail=str(err))


@app.post("/process_eonet_events")
async def read_pseudo_stream_eonet_events():
    api = NasaEonetEventAPI()

    dt = DisasterTracker(EventStorage("disaster_events"))

    match await api.fetch_events():
        case Ok(disasters):
            match await dt.save_many(disasters):
                case Ok(True): return dict(status_code=201)
                case Ok(False): return dict(status_code=204)
                case Err(err): raise HTTPException(status_code=500, detail=str(err))

        case Err(err): raise HTTPException(status_code=500, detail=str(err))
