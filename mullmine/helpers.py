import logging

from django.contrib.postgres.aggregates import ArrayAgg
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from django.db.models import (
    F,
    Count,
    Q,
    Case,
    When,
    FloatField,
    OuterRef,
    Max,
    BooleanField,
)
from django.db.models.functions import Cast
from django.db.models.lookups import GreaterThan
from firebase_admin.auth import delete_user as delete_firebase_user

from mullmine.models import Room, Message, User, Conversation, ReportedChat

NUM_MESSAGES_PER_PAGE = 10
FULL_ROOM_NUM_MEMBERS = 5


def create_room(question):
    room = Room.objects.create(question=question)
    return {"room": room.id}


def get_question(room):
    return room.question


def get_all_room_ids(user):
    room_ids = list(user.room_set.all().values_list("id", flat=True))
    return room_ids


def set_online(username):
    User.objects.filter(username=username).update(is_online=True)


def set_offline(username):
    User.objects.filter(username=username).update(is_online=False)


def delete_user(user):
    delete_firebase_user(user.username)
    user.delete()


def block_room_user(room_id, user, username):
    room = Room.objects.filter(id=room_id).first()
    if room:
        blocked_user = room.members.filter(username=username).first()
        if blocked_user and room.members.filter(username=user.username).exists():
            user.blocked_users.add(blocked_user)


def log_reported_chat(room, reporter, reported_username):
    reported = User.objects.get(username=reported_username)
    ReportedChat.objects.get_or_create(
        reported_room=room, reporter=reporter, reported=reported
    )


def report_room_user(room_id, user, username):
    room = Room.objects.filter(id=room_id).first()
    if room:
        reported_user = room.members.filter(username=username).first()
        if reported_user and room.members.filter(username=user.username).exists():
            user.reported_users.add(reported_user)
            log_reported_chat(room, user, reported_user)


def leave_room(user, room_id):
    room_to_leave = Room.objects.get(id=room_id)
    if user in room_to_leave.members.all():
        user.conversation_set.filter(room=room_to_leave).delete()
        room_to_leave.members.remove(user)
        if room_to_leave.members.count() == 0:
            room_to_leave.delete()


def read_unread_conversation(room_id, user):
    room = Room.objects.get(id=room_id)
    conversation = Conversation.objects.get(participant=user, room=room)
    if not conversation.read:
        Conversation.objects.filter(id=conversation.id).update(read=True)


def get_user_conversations(username):
    user = User.objects.get(username=username)
    conversations = list(
        user.conversation_set.values(
            "room__id",
            "room__question",
            "read",
            "latest_message__creator__display_name",
            "latest_message__content",
            "latest_message__created_at",
            "created_at",
        ).order_by(
            "read", F("latest_message__created_at").desc(nulls_last=True), "-created_at"
        )
    )
    for conversation in conversations:
        conversation["room__id"] = str(conversation["room__id"])
        conversation["created_at"] = conversation["created_at"].timestamp()
        if conversation["latest_message__created_at"]:
            conversation["latest_message__created_at"] = conversation[
                "latest_message__created_at"
            ].timestamp()
    return conversations


def check_room_full(room_id, user):
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        is_member = room.members.filter(username=user.username).exists()
        return room.members.all().count() >= FULL_ROOM_NUM_MEMBERS and not is_member


def get_room(room_id):
    try:
        room = Room.objects.get(id=room_id)
        return room
    except ObjectDoesNotExist:
        logging.error(f"Room id {room_id} does not exist")


def get_active_questions(user):
    most_chatted_users = get_most_chatted_users_of_most_chatted_users(user)
    num_most_chatted_users = Count(
        "members", filter=Q(members__in=most_chatted_users), distinct=True
    )
    blocked_users_ids = user.blocked_users.all().values_list("id", flat=True)
    num_blocked_users = Count(
        "members", filter=Q(members__id__in=blocked_users_ids), distinct=True
    )
    num_members = Count("members", distinct=True)
    num_members_online = Count(
        "members", filter=Q(members__is_online=True), distinct=True
    )
    rooms = (
        Room.objects.annotate(num_members=num_members)
        .annotate(num_most_chatted_users=num_most_chatted_users)
        .annotate(num_members_online=num_members_online)
        .annotate(num_blocked_users=num_blocked_users)
        .annotate(latest_msg=Max("message__created_at"))
        .filter(
            num_members__lt=FULL_ROOM_NUM_MEMBERS,
            num_blocked_users=0,
        )
        .order_by(
            "-num_most_chatted_users",
            "-num_members_online",
            F("latest_msg").desc(nulls_last=True),
            "-created_at",
            "-num_members",
        )
        .values()[:10]
    )
    return [{"question": room["question"], "id": str(room["id"])} for room in rooms]


def get_most_chatted_users(user, exclude_room_ids=None):
    num_members = Count("members", distinct=True)
    num_messages = Count("message", distinct=True)
    num_your_messages = Count("message", filter=Q(message__creator=user), distinct=True)
    num_not_your_messages = Count(
        "message", filter=~Q(message__creator=user), distinct=True
    )
    chattiness_score = Case(
        When(
            GreaterThan(F("num_your_messages"), 0)
            & GreaterThan(F("num_not_your_messages"), 0),
            then=Cast(F("num_messages"), FloatField())
            * Cast(F("num_your_messages"), FloatField())
            / Cast(F("num_not_your_messages"), FloatField()),
        ),
        default=0,
        output_field=FloatField(),
    )
    other_members_ids = ArrayAgg(
        "members__id",
        filter=~Q(members__id=user.id),
        distinct=True,
    )
    your_chattiest_rooms = (
        Room.objects.annotate(num_members=num_members)
        .annotate(num_messages=num_messages)
        .annotate(num_your_messages=num_your_messages)
        .annotate(num_not_your_messages=num_not_your_messages)
        .annotate(num_not_your_messages=num_not_your_messages)
        .annotate(chattiness_score=chattiness_score)
        .annotate(other_members_ids=other_members_ids)
        .filter(members=user, num_members__lte=FULL_ROOM_NUM_MEMBERS)
        .order_by("-chattiness_score")
        .values("other_members_ids")
    )
    if exclude_room_ids:
        your_chattiest_rooms = your_chattiest_rooms.exclude(id__in=exclude_room_ids)
    members_ids = set()
    for room in your_chattiest_rooms[:5]:
        for member_id in room["other_members_ids"]:
            members_ids.add(member_id)
    return User.objects.filter(id__in=members_ids)


def get_most_chatted_users_of_most_chatted_users(user):
    top_most_chatted_users = get_most_chatted_users(user)
    exclude_room_ids = user.room_set.all().values_list("id", flat=True)
    users = User.objects.none()
    for top_user in top_most_chatted_users:
        top_user_most_chatted_users = get_most_chatted_users(
            top_user, exclude_room_ids=exclude_room_ids
        )
        if not users:
            users = top_user_most_chatted_users
        else:
            users.union(top_user_most_chatted_users)
    return users


def get_all_chats(user, question):
    blocked_users_ids = user.blocked_users.all().values_list("id", flat=True)
    num_blocked_users = Count(
        "members", filter=Q(members__id__in=blocked_users_ids), distinct=True
    )
    num_members = Count("members", distinct=True)
    num_members_online = Count(
        "members", filter=Q(members__is_online=True), distinct=True
    )
    most_chatted_users = get_most_chatted_users_of_most_chatted_users(user)
    num_most_chatted_users = Count(
        "members", filter=Q(members__in=most_chatted_users), distinct=True
    )
    user_rooms_ids = user.room_set.all().values_list("id", flat=True)
    already_joined = Case(
        When(id__in=user_rooms_ids, then=True), output_field=BooleanField()
    )
    latest_message_timestamp = (
        Message.objects.filter(room__id=OuterRef("id"))
        .order_by("-created_at")
        .values("created_at")[:1]
    )
    rooms = (
        Room.objects.all()
        .annotate(num_members=num_members)
        .annotate(num_members_online=num_members_online)
        .annotate(num_blocked_users=num_blocked_users)
        .annotate(num_most_chatted_users=num_most_chatted_users)
        .annotate(latest_message_timestamp=latest_message_timestamp)
        .annotate(already_joined=already_joined)
        .filter(
            num_members__lt=FULL_ROOM_NUM_MEMBERS,
            num_blocked_users=0,
            question__icontains=question,
        )
        .order_by(
            "already_joined",
            "-num_most_chatted_users",
            "-num_members_online",
            F("latest_message_timestamp").desc(nulls_last=True),
            "-created_at",
        )
    )
    return rooms


def find_rooms(user, question):
    rooms = get_all_chats(user, question)
    rooms = rooms[:10]
    return [
        {
            "pk": str(room.id),
            "question": room.question,
            "latest_message_timestamp": (
                room.latest_message_timestamp.timestamp()
                if hasattr(room, "latest_message_timestamp")
                and room.latest_message_timestamp
                else None
            ),
            "created_at": room.created_at.timestamp(),
        }
        for room in rooms
    ]


def suggest_questions(user, question):
    rooms = get_all_chats(user, question)
    suggestions = []
    for room in rooms:
        if room.question not in suggestions:
            suggestions.append(room.question)
        if len(suggestions) == 10:
            break
    return suggestions


def initialize_room(room_id, user):
    user = User.objects.get(username=user.username)
    if user.is_verified:
        if room_id:
            try:
                room = Room.objects.get(id=room_id)
                return room
            except ObjectDoesNotExist:
                return None


def get_user(username):
    user = User.objects.get(username=username)
    return user


def get_all_members(room_id):
    members = []
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        members = room.members.values("display_name", "is_online", "username")
    return [
        {
            "name": member["display_name"],
            "is_online": member["is_online"],
            "username": member["username"],
        }
        for member in members
    ]


def get_all_member_usernames(room_id):
    members = []
    room = Room.objects.filter(id=room_id)
    if room.exists():
        room = room.first()
        members = room.members.all().values("username")
    return [member["username"] for member in members]


def add_user_to_room(user, room):
    was_added = False
    latest_message = room.message_set.order_by("-created_at").first()
    if user not in room.members.all():
        room.members.add(user)
        Conversation.objects.create(
            participant=user, room=room, latest_message=latest_message, read=True
        )
        was_added = True
    members = get_all_members(room.id)
    return members, was_added


def get_initial_messages(room, user):
    blocked_user_ids = user.blocked_users.all().values_list("id", flat=True)
    messages = [
        {
            "creator_username": msg.creator.username,
            "creator_display_name": msg.creator.display_name,
            "content": msg.content,
            "created_at": msg.created_at.timestamp(),
            "id": str(msg.id),
        }
        for msg in room.message_set.exclude(creator__id__in=blocked_user_ids).order_by(
            "-created_at"
        )[:NUM_MESSAGES_PER_PAGE][::-1]
    ]
    return messages


def get_refreshed_messages(room, oldest_message_timestamp, user):
    blocked_user_ids = user.blocked_users.all().values_list("id", flat=True)
    if oldest_message_timestamp:
        messages = [
            {
                "creator_username": msg.creator.username,
                "creator_display_name": msg.creator.display_name,
                "content": msg.content,
                "created_at": msg.created_at.timestamp(),
                "id": str(msg.id),
            }
            for msg in room.message_set.filter(
                created_at__gte=oldest_message_timestamp,
            )
            .exclude(creator__id__in=blocked_user_ids)
            .order_by("-created_at")[::-1]
        ]
    else:
        messages = []
    return messages


def create_new_message(content, room, creator):

    new_message = Message.objects.create(creator=creator, room=room, content=content)
    update_conversations_for_new_message(room, new_message)
    return {
        "creator_username": new_message.creator.username,
        "creator_display_name": new_message.creator.display_name,
        "content": new_message.content,
        "created_at": new_message.created_at.timestamp(),
        "id": str(new_message.id),
    }


def is_blocked_creator(user, username):
    creator = User.objects.get(username=username)
    return creator in user.blocked_users.all()


def update_conversations_for_new_message(room, message):
    for user in room.members.all():
        if message.creator not in user.blocked_users.all():
            conversation = Conversation.objects.get(participant=user, room=room)
            Conversation.objects.filter(id=conversation.id).update(
                latest_message=message, read=user == message.creator
            )


def get_prev_messages(oldest_msg_id, room, user):
    blocked_user_ids = user.blocked_users.all().values_list("id", flat=True)
    messages = []
    oldest_msg = Message.objects.filter(id=oldest_msg_id, room=room)
    if oldest_msg.exists():
        oldest_msg = oldest_msg.first()
        messages = [
            {
                "creator_username": msg.creator.username,
                "creator_display_name": msg.creator.display_name,
                "content": msg.content,
                "created_at": msg.created_at.timestamp(),
                "id": str(msg.id),
            }
            for msg in room.message_set.filter(created_at__lt=oldest_msg.created_at)
            .exclude(creator__id__in=blocked_user_ids)
            .order_by("-created_at")[:NUM_MESSAGES_PER_PAGE][::-1]
        ]
    return messages


def change_user_display_name(user, new_name):
    rooms_to_refresh = [str(room["id"]) for room in user.room_set.all().values()]
    users_to_refresh = [
        str(conversation["participant__username"])
        for conversation in Conversation.objects.filter(
            latest_message__creator=user
        ).values("participant__username")
    ]
    try:
        User.objects.filter(id=user.id).update(display_name=new_name)
        return True, new_name, rooms_to_refresh, users_to_refresh
    except IntegrityError:
        return False, new_name, rooms_to_refresh, users_to_refresh
