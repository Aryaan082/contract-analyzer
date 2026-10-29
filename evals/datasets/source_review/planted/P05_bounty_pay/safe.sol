// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IPayToken {
    function transfer(address to, uint256 amount) external returns (bool);
}

contract BountyBoard {
    IPayToken public immutable token;
    address public judge;
    mapping(uint256 => uint256) public reward;
    mapping(uint256 => bool) public paid;

    constructor(IPayToken token_, address judge_) {
        token = token_;
        judge = judge_;
    }

    function post(uint256 id, uint256 amount) external {
        require(msg.sender == judge, "not judge");
        reward[id] = amount;
    }

    function pay(uint256 id, address winner) external {
        require(msg.sender == judge, "not judge");
        require(!paid[id], "already paid");
        paid[id] = true;
        require(token.transfer(winner, reward[id]), "transfer failed");
    }
}
