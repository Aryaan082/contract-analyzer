// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IRandomnessFeed {
    function latestWord() external view returns (uint256);
}

contract TicketRaffle {
    IRandomnessFeed public randomness;
    address[] public players;
    uint256 public ticketPrice = 0.1 ether;

    function enter() external payable {
        require(msg.value == ticketPrice, "wrong price");
        players.push(msg.sender);
    }

    function draw() external {
        require(players.length >= 3, "too few players");
        uint256 pick = randomness.latestWord() % players.length;
        address winner = players[pick];
        delete players;
        payable(winner).transfer(address(this).balance);
    }
}
