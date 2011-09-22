import QtQuick 1.0

Item {
    anchors.fill: parent;

    Text {
        id: genre
        text: "Genre: <b>" + (tags.genre || "")
        font.pointSize: 11
    }

    Text {
        id: rating
        text: "Rating: <b>" + (tags.episode_rating || "")
        font.pointSize: 11
        anchors {
            left: genre.right
            leftMargin: 4
        }
    }
}